# AWS Infrastructure Setup Guide — Enterprise Agentic RAG

> **Scope:** Build the full AWS backend for the `deployment` branch using the AWS CLI.  
> **Approach:** Option A — managed Qdrant Cloud, Neon PostgreSQL, Upstash Redis, ECS Fargate, and Application Load Balancer.

Run the commands from a terminal with an IAM user that has **AdministratorAccess** (or equivalent). AWS CLI v2 and `jq` are required.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Variables](#2-variables)
3. [VPC & Networking](#3-vpc--networking)
4. [Security Groups](#4-security-groups)
5. [Neon PostgreSQL + Upstash Redis](#5-neon-postgresql--upstash-redis)
6. [Qdrant Cloud](#6-qdrant-cloud)
7. [ECR Repository](#7-ecr-repository)
8. [CloudWatch Log Groups](#8-cloudwatch-log-groups)
9. [Secrets Manager](#9-secrets-manager)
10. [IAM Roles](#10-iam-roles)
11. [ECS Cluster](#11-ecs-cluster)
12. [Task Definitions](#12-task-definitions)
13. [Application Load Balancer](#13-application-load-balancer)
14. [ECS Services](#14-ecs-services)
15. [Auto Scaling](#15-auto-scaling)
16. [GitHub Secrets](#16-github-secrets)
17. [Push & Trigger CI/CD](#17-push--trigger-cicd)
18. [Validation](#18-validation)
19. [Cleanup](#19-cleanup)

---

## 1. Prerequisites

- AWS CLI v2 installed and configured: `aws configure`
- `jq` installed: `brew install jq` or `sudo apt-get install jq`
- A default region chosen (used throughout this guide)
- Admin IAM credentials exported **or** configured via `aws configure`

```bash
aws sts get-caller-identity
```

You should see your account, user, and ARN.

---

## 2. Variables

Set these once at the start of your session. All later commands use them.

```bash
export AWS_REGION="us-east-1"
export PROJECT="rag"
export VPC_CIDR="10.0.0.0/16"
export PUBLIC_SUBNET_1_CIDR="10.0.1.0/24"
export PUBLIC_SUBNET_2_CIDR="10.0.2.0/24"
export PRIVATE_SUBNET_1_CIDR="10.0.3.0/24"
export PRIVATE_SUBNET_2_CIDR="10.0.4.0/24"

# Derived names — change if you already have resources with these names
export VPC_NAME="${PROJECT}-vpc"
export ALB_NAME="${PROJECT}-alb"
export ECR_REPO="enterprise-rag"
export ECS_CLUSTER="${PROJECT}-cluster"
```

---

## 3. VPC & Networking

### 3.1 Create VPC

```bash
export VPC_ID=$(aws ec2 create-vpc \
  --cidr-block $VPC_CIDR \
  --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=$VPC_NAME}]" \
  --query 'Vpc.VpcId' \
  --output text)
echo "VPC_ID=$VPC_ID"

# Enable DNS hostnames (required for ALB and service endpoints)
aws ec2 modify-vpc-attribute \
  --vpc-id $VPC_ID \
  --enable-dns-hostnames
```

### 3.2 Create Internet Gateway

```bash
export IGW_ID=$(aws ec2 create-internet-gateway \
  --tag-specifications "ResourceType=internet-gateway,Tags=[{Key=Name,Value=$PROJECT-igw}]" \
  --query 'InternetGateway.InternetGatewayId' \
  --output text)
echo "IGW_ID=$IGW_ID"

aws ec2 attach-internet-gateway \
  --internet-gateway-id $IGW_ID \
  --vpc-id $VPC_ID
```

### 3.3 Get Availability Zones

```bash
export AZS=$(aws ec2 describe-availability-zones \
  --query 'AvailabilityZones[*].ZoneName' \
  --output text)
echo "AZS=$AZS"
export AZ1=$(echo $AZS | awk '{print $1}')
export AZ2=$(echo $AZS | awk '{print $2}')
echo "AZ1=$AZ1, AZ2=$AZ2"
```

### 3.4 Create Subnets

```bash
export PUBLIC_SUBNET_1=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID \
  --cidr-block $PUBLIC_SUBNET_1_CIDR \
  --availability-zone $AZ1 \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$PROJECT-public-$AZ1}]" \
  --query 'Subnet.SubnetId' \
  --output text)

export PUBLIC_SUBNET_2=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID \
  --cidr-block $PUBLIC_SUBNET_2_CIDR \
  --availability-zone $AZ2 \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$PROJECT-public-$AZ2}]" \
  --query 'Subnet.SubnetId' \
  --output text)

export PRIVATE_SUBNET_1=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID \
  --cidr-block $PRIVATE_SUBNET_1_CIDR \
  --availability-zone $AZ1 \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$PROJECT-private-$AZ1}]" \
  --query 'Subnet.SubnetId' \
  --output text)

export PRIVATE_SUBNET_2=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID \
  --cidr-block $PRIVATE_SUBNET_2_CIDR \
  --availability-zone $AZ2 \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$PROJECT-private-$AZ2}]" \
  --query 'Subnet.SubnetId' \
  --output text)

echo "PUBLIC_SUBNET_1=$PUBLIC_SUBNET_1"
echo "PUBLIC_SUBNET_2=$PUBLIC_SUBNET_2"
echo "PRIVATE_SUBNET_1=$PRIVATE_SUBNET_1"
echo "PRIVATE_SUBNET_2=$PRIVATE_SUBNET_2"
```

### 3.5 Create NAT Gateways

```bash
export EIP_1=$(aws ec2 allocate-address \
  --domain vpc \
  --query 'AllocationId' \
  --output text)

export NAT_GW_1=$(aws ec2 create-nat-gateway \
  --subnet-id $PUBLIC_SUBNET_1 \
  --allocation-id $EIP_1 \
  --tag-specifications "ResourceType=natgateway,Tags=[{Key=Name,Value=$PROJECT-nat-$AZ1}]" \
  --query 'NatGateway.NatGatewayId' \
  --output text)

echo "NAT_GW_1=$NAT_GW_1"

# Wait for NAT gateway to become available
aws ec2 wait nat-gateway-available --nat-gateway-ids $NAT_GW_1
```

> **Note:** In production, create a second NAT Gateway in the second public subnet for AZ redundancy. For a minimal setup, one NAT Gateway is enough.

### 3.6 Route Tables

**Public route table:**

```bash
export PUBLIC_RT=$(aws ec2 create-route-table \
  --vpc-id $VPC_ID \
  --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=$PROJECT-public-rt}]" \
  --query 'RouteTable.RouteTableId' \
  --output text)

aws ec2 create-route \
  --route-table-id $PUBLIC_RT \
  --destination-cidr-block 0.0.0.0/0 \
  --gateway-id $IGW_ID

aws ec2 associate-route-table \
  --route-table-id $PUBLIC_RT \
  --subnet-id $PUBLIC_SUBNET_1

aws ec2 associate-route-table \
  --route-table-id $PUBLIC_RT \
  --subnet-id $PUBLIC_SUBNET_2
```

**Private route table:**

```bash
export PRIVATE_RT=$(aws ec2 create-route-table \
  --vpc-id $VPC_ID \
  --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=$PROJECT-private-rt}]" \
  --query 'RouteTable.RouteTableId' \
  --output text)

aws ec2 create-route \
  --route-table-id $PRIVATE_RT \
  --destination-cidr-block 0.0.0.0/0 \
  --nat-gateway-id $NAT_GW_1

aws ec2 associate-route-table \
  --route-table-id $PRIVATE_RT \
  --subnet-id $PRIVATE_SUBNET_1

aws ec2 associate-route-table \
  --route-table-id $PRIVATE_RT \
  --subnet-id $PRIVATE_SUBNET_2
```

---

## 4. Security Groups

```bash
# ALB security group
export ALB_SG=$(aws ec2 create-security-group \
  --group-name "${PROJECT}-alb-sg" \
  --description "ALB security group" \
  --vpc-id $VPC_ID \
  --query 'GroupId' \
  --output text)

aws ec2 authorize-security-group-ingress \
  --group-id $ALB_SG \
  --protocol tcp \
  --port 80 \
  --cidr 0.0.0.0/0

aws ec2 authorize-security-group-ingress \
  --group-id $ALB_SG \
  --protocol tcp \
  --port 443 \
  --cidr 0.0.0.0/0

# API service security group
export API_SG=$(aws ec2 create-security-group \
  --group-name "${PROJECT}-api-sg" \
  --description "RAG API task security group" \
  --vpc-id $VPC_ID \
  --query 'GroupId' \
  --output text)

aws ec2 authorize-security-group-ingress \
  --group-id $API_SG \
  --protocol tcp \
  --port 8080 \
  --source-group $ALB_SG

# UI security group
export UI_SG=$(aws ec2 create-security-group \
  --group-name "${PROJECT}-ui-sg" \
  --description "RAG UI task security group" \
  --vpc-id $VPC_ID \
  --query 'GroupId' \
  --output text)

aws ec2 authorize-security-group-ingress \
  --group-id $UI_SG \
  --protocol tcp \
  --port 8501 \
  --source-group $ALB_SG

echo "ALB_SG=$ALB_SG"
echo "API_SG=$API_SG"
echo "UI_SG=$UI_SG"
```

> **Note:** Neon and Upstash are managed services accessed over HTTPS from the private subnets via the NAT Gateway. No Redis or PostgreSQL security groups are required.

---

## 5. Neon PostgreSQL + Upstash Redis

State is hosted in managed services outside AWS. Create both services in consoles, then export the connection details below.

### 5.1 Neon PostgreSQL

1. Go to [https://console.neon.tech](https://console.neon.tech) and sign up/log in.
2. Create a new project (choose a region close to your AWS region).
3. Create a database named `enterprise_rag` (or use the default Neon database).
4. Copy the PostgreSQL connection string for the database.

Export it locally:

```bash
export NEON_DB_URL="postgresql://user:password@host.neon.tech/enterprise_rag?sslmode=require"
```

### 5.2 Upstash Redis

1. Go to [https://console.upstash.com](https://console.upstash.com) and sign up/log in.
2. Create a new Redis database (choose a region close to your AWS region).
3. Enable the **REST API** and copy:
   - **REST URL** (e.g., `https://prompt-amoeba-12345.upstash.io`)
   - **REST TOKEN**

Export them locally:

```bash
export UPSTASH_REDIS_REST_URL="https://your-db.upstash.io"
export UPSTASH_REDIS_REST_TOKEN="your-upstash-rest-token"
```

---

## 6. Qdrant Cloud

Qdrant Cloud cannot be created via AWS CLI. Complete these steps in the Qdrant Cloud console:

1. Go to [https://cloud.qdrant.io](https://cloud.qdrant.io) and sign up/log in.
2. Create a new cluster (choose a region close to your AWS region).
3. Once the cluster is running, copy:
   - **Cluster URL** (e.g., `https://xyz-example.eu-central-1-0.aws.cloud.qdrant.io:6333`)
   - **API Key**
4. Create a collection named `enterprise_rag` with vector size `3072` and distance `Cosine`.

Export the values locally:

```bash
export QDRANT_URL="https://your-cluster-url.qdrant.io:6333"
export QDRANT_API_KEY="your-qdrant-api-key"
```

---

## 7. ECR Repository

```bash
aws ecr create-repository \
  --repository-name $ECR_REPO \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256

export ECR_URI=$(aws ecr describe-repositories \
  --repository-names $ECR_REPO \
  --query 'repositories[0].repositoryUri' \
  --output text)
echo "ECR_URI=$ECR_URI"

# Lifecycle policy: keep last 30 images
aws ecr put-lifecycle-policy \
  --repository-name $ECR_REPO \
  --lifecycle-policy-text '{
    "rules": [{
      "rulePriority": 1,
      "description": "Keep last 30 images",
      "selection": {
        "tagStatus": "any",
        "countType": "imageCountMoreThan",
        "countNumber": 30
      },
      "action": { "type": "expire" }
    }]
  }'
```

---

## 8. CloudWatch Log Groups

```bash
aws logs create-log-group --log-group-name /ecs/rag-api
aws logs create-log-group --log-group-name /ecs/rag-ui
```

---

## 9. Secrets Manager

Create one secret per environment variable. These commands use the values you exported earlier.

```bash
export NEON_DB_URL_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/neon-db-url" \
  --description "Neon PostgreSQL connection string for LangGraph checkpointer" \
  --secret-string "$NEON_DB_URL" \
  --query 'ARN' --output text)

export UPSTASH_REDIS_REST_URL_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/upstash-redis-rest-url" \
  --description "Upstash Redis REST URL for rate limiting" \
  --secret-string "$UPSTASH_REDIS_REST_URL" \
  --query 'ARN' --output text)

export UPSTASH_REDIS_REST_TOKEN_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/upstash-redis-rest-token" \
  --description "Upstash Redis REST token" \
  --secret-string "$UPSTASH_REDIS_REST_TOKEN" \
  --query 'ARN' --output text)

export QDRANT_URL_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/qdrant-url" \
  --description "Qdrant cluster endpoint" \
  --secret-string "$QDRANT_URL" \
  --query 'ARN' --output text)

export QDRANT_API_KEY_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/qdrant-api-key" \
  --description "Qdrant API key" \
  --secret-string "$QDRANT_API_KEY" \
  --query 'ARN' --output text)

# Repeat for the remaining secrets. Set the values first, then run create-secret.
export OPENAI_API_KEY="your-openai-key"
export JINA_API_KEY="your-jina-key"
export PORTKEY_API_KEY="your-portkey-key"
export RAG_API_KEY="your-production-api-key"
export LOGFIRE_TOKEN="your-logfire-token"
export LANGSMITH_API_KEY="your-langsmith-key"

export OPENAI_API_KEY_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/openai-api-key" \
  --secret-string "$OPENAI_API_KEY" \
  --query 'ARN' --output text)

export JINA_API_KEY_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/jina-api-key" \
  --secret-string "$JINA_API_KEY" \
  --query 'ARN' --output text)

export PORTKEY_API_KEY_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/portkey-api-key" \
  --secret-string "$PORTKEY_API_KEY" \
  --query 'ARN' --output text)

export RAG_API_KEY_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/rag-api-key" \
  --secret-string "$RAG_API_KEY" \
  --query 'ARN' --output text)

export LOGFIRE_TOKEN_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/logfire-token" \
  --secret-string "$LOGFIRE_TOKEN" \
  --query 'ARN' --output text)

export LANGSMITH_API_KEY_ARN=$(aws secretsmanager create-secret \
  --name "${PROJECT}/langsmith-api-key" \
  --secret-string "$LANGSMITH_API_KEY" \
  --query 'ARN' --output text)

echo "NEON_DB_URL_ARN=$NEON_DB_URL_ARN"
echo "UPSTASH_REDIS_REST_URL_ARN=$UPSTASH_REDIS_REST_URL_ARN"
echo "UPSTASH_REDIS_REST_TOKEN_ARN=$UPSTASH_REDIS_REST_TOKEN_ARN"
echo "QDRANT_URL_ARN=$QDRANT_URL_ARN"
echo "QDRANT_API_KEY_ARN=$QDRANT_API_KEY_ARN"
echo "OPENAI_API_KEY_ARN=$OPENAI_API_KEY_ARN"
echo "JINA_API_KEY_ARN=$JINA_API_KEY_ARN"
echo "PORTKEY_API_KEY_ARN=$PORTKEY_API_KEY_ARN"
echo "RAG_API_KEY_ARN=$RAG_API_KEY_ARN"
echo "LOGFIRE_TOKEN_ARN=$LOGFIRE_TOKEN_ARN"
echo "LANGSMITH_API_KEY_ARN=$LANGSMITH_API_KEY_ARN"
```

**Save these ARNs.** You will paste them into GitHub secrets.

---

## 10. IAM Roles

### 10.1 Ensure ECS Task Execution Role Exists

```bash
aws iam get-role --role-name ecsTaskExecutionRole >/dev/null 2>&1 || \
aws iam create-role \
  --role-name ecsTaskExecutionRole \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "ecs-tasks.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

aws iam attach-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

### 10.2 Create Task Roles

Create a trust policy file:

```bash
cat > /tmp/ecs-trust-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF
```

Create the roles:

```bash
aws iam create-role \
  --role-name rag-api-task-role \
  --assume-role-policy-document file:///tmp/ecs-trust-policy.json

aws iam create-role \
  --role-name rag-ui-task-role \
  --assume-role-policy-document file:///tmp/ecs-trust-policy.json
```

Create a policy that allows reading only the project secrets:

```bash
cat > /tmp/rag-secrets-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["secretsmanager:GetSecretValue"],
    "Resource": [
      "$NEON_DB_URL_ARN",
      "$UPSTASH_REDIS_REST_URL_ARN",
      "$UPSTASH_REDIS_REST_TOKEN_ARN",
      "$QDRANT_URL_ARN",
      "$QDRANT_API_KEY_ARN",
      "$OPENAI_API_KEY_ARN",
      "$JINA_API_KEY_ARN",
      "$PORTKEY_API_KEY_ARN",
      "$RAG_API_KEY_ARN",
      "$LOGFIRE_TOKEN_ARN",
      "$LANGSMITH_API_KEY_ARN"
    ]
  }]
}
EOF

export SECRETS_POLICY_ARN=$(aws iam create-policy \
  --policy-name rag-read-secrets-policy \
  --policy-document file:///tmp/rag-secrets-policy.json \
  --query 'Policy.Arn' \
  --output text)

for ROLE in rag-api-task-role rag-ui-task-role; do
  aws iam attach-role-policy \
    --role-name $ROLE \
    --policy-arn $SECRETS_POLICY_ARN
done
```

---

## 11. ECS Cluster

```bash
aws ecs create-cluster \
  --cluster-name $ECS_CLUSTER \
  --capacity-providers FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy capacityProvider=FARGATE,weight=1 \
  --settings name=containerInsights,value=enabled
```

---

## 12. Task Definitions

Render the placeholder task definitions and register them.

```bash
export IMAGE_URI="${ECR_URI}:latest"

render() {
  sed \
    -e "s|<IMAGE_NAME>|$IMAGE_URI|g" \
    -e "s|<AWS_REGION>|$AWS_REGION|g" \
    -e "s|<NEON_DB_URL_ARN>|$NEON_DB_URL_ARN|g" \
    -e "s|<UPSTASH_REDIS_REST_URL_ARN>|$UPSTASH_REDIS_REST_URL_ARN|g" \
    -e "s|<UPSTASH_REDIS_REST_TOKEN_ARN>|$UPSTASH_REDIS_REST_TOKEN_ARN|g" \
    -e "s|<QDRANT_URL_ARN>|$QDRANT_URL_ARN|g" \
    -e "s|<QDRANT_API_KEY_ARN>|$QDRANT_API_KEY_ARN|g" \
    -e "s|<OPENAI_API_KEY_ARN>|$OPENAI_API_KEY_ARN|g" \
    -e "s|<JINA_API_KEY_ARN>|$JINA_API_KEY_ARN|g" \
    -e "s|<PORTKEY_API_KEY_ARN>|$PORTKEY_API_KEY_ARN|g" \
    -e "s|<RAG_API_KEY_ARN>|$RAG_API_KEY_ARN|g" \
    -e "s|<LOGFIRE_TOKEN_ARN>|$LOGFIRE_TOKEN_ARN|g" \
    -e "s|<LANGSMITH_API_KEY_ARN>|$LANGSMITH_API_KEY_ARN|g" \
    "$1" > "$2"
}

render .aws/task-definitions/rag-api.json    /tmp/rag-api.json
render .aws/task-definitions/rag-ui.json     /tmp/rag-ui.json

export RAG_API_TASK_DEF_ARN=$(aws ecs register-task-definition \
  --cli-input-json file:///tmp/rag-api.json \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

export RAG_UI_TASK_DEF_ARN=$(aws ecs register-task-definition \
  --cli-input-json file:///tmp/rag-ui.json \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

echo "RAG_API_TASK_DEF_ARN=$RAG_API_TASK_DEF_ARN"
echo "RAG_UI_TASK_DEF_ARN=$RAG_UI_TASK_DEF_ARN"
```

---

## 13. Application Load Balancer

### 13.1 Create ALB

```bash
export ALB_ARN=$(aws elbv2 create-load-balancer \
  --name $ALB_NAME \
  --type application \
  --scheme internet-facing \
  --security-groups $ALB_SG \
  --subnets $PUBLIC_SUBNET_1 $PUBLIC_SUBNET_2 \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text)

export ALB_DNS=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns $ALB_ARN \
  --query 'LoadBalancers[0].DNSName' \
  --output text)

echo "ALB_DNS=$ALB_DNS"
```

### 13.2 Create Target Groups

```bash
export API_TG_ARN=$(aws elbv2 create-target-group \
  --name "${PROJECT}-api-tg" \
  --protocol HTTP \
  --port 8080 \
  --vpc-id $VPC_ID \
  --target-type ip \
  --health-check-path /health \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text)

export UI_TG_ARN=$(aws elbv2 create-target-group \
  --name "${PROJECT}-ui-tg" \
  --protocol HTTP \
  --port 8501 \
  --vpc-id $VPC_ID \
  --target-type ip \
  --health-check-path /_stcore/health \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text)
```

### 13.3 Create Listener

```bash
export LISTENER_ARN=$(aws elbv2 create-listener \
  --load-balancer-arn $ALB_ARN \
  --protocol HTTP \
  --port 80 \
  --default-actions Type=forward,TargetGroupArn=$API_TG_ARN \
  --query 'Listeners[0].ListenerArn' \
  --output text)

# Forward /ui* to the Streamlit UI target group
aws elbv2 create-rule \
  --listener-arn $LISTENER_ARN \
  --priority 10 \
  --conditions Field=path-pattern,PathPatternConfig='{Values=["/ui*"]}' \
  --actions Type=forward,TargetGroupArn=$UI_TG_ARN
```

---

## 14. ECS Services

### 14.1 API Service

```bash
aws ecs create-service \
  --cluster $ECS_CLUSTER \
  --service-name rag-api \
  --task-definition $RAG_API_TASK_DEF_ARN \
  --desired-count 2 \
  --launch-type FARGATE \
  --platform-version LATEST \
  --network-configuration "awsvpcConfiguration={subnets=[$PRIVATE_SUBNET_1,$PRIVATE_SUBNET_2],securityGroups=[$API_SG],assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=$API_TG_ARN,containerName=api,containerPort=8080" \
  --health-check-grace-period-seconds 60 \
  --deployment-configuration "minimumHealthyPercent=100,maximumPercent=200"
```

### 14.2 UI Service

```bash
aws ecs create-service \
  --cluster $ECS_CLUSTER \
  --service-name rag-ui \
  --task-definition $RAG_UI_TASK_DEF_ARN \
  --desired-count 1 \
  --launch-type FARGATE \
  --platform-version LATEST \
  --network-configuration "awsvpcConfiguration={subnets=[$PRIVATE_SUBNET_1,$PRIVATE_SUBNET_2],securityGroups=[$UI_SG],assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=$UI_TG_ARN,containerName=ui,containerPort=8501" \
  --health-check-grace-period-seconds 60 \
  --deployment-configuration "minimumHealthyPercent=100,maximumPercent=200"
```

---

## 15. Auto Scaling

### 15.1 API Auto Scaling

```bash
aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --resource-id "service/${ECS_CLUSTER}/rag-api" \
  --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 2 \
  --max-capacity 10 \
  --role-name ecsAutoscaleRole

aws application-autoscaling put-scaling-policy \
  --service-namespace ecs \
  --resource-id "service/${ECS_CLUSTER}/rag-api" \
  --scalable-dimension ecs:service:DesiredCount \
  --policy-name rag-api-request-count \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration '{
    "PredefinedMetricSpecification": {
      "PredefinedMetricType": "ALBRequestCountPerTarget",
      "ResourceLabel": "app/'$ALB_NAME'/'$(echo $ALB_ARN | awk -F/ '{print $NF}')'/targetgroup/'$(echo $API_TG_ARN | awk -F/ '{print $NF}')'"
    },
    "TargetValue": 1000.0,
    "ScaleOutCooldown": 60,
    "ScaleInCooldown": 300
  }'
```

> **Tip:** The `ResourceLabel` format is `app/<alb-name>/<alb-id>/targetgroup/<tg-name>/<tg-id>`. If the command above fails, copy the exact label from the AWS Console (EC2 → Target Groups → Monitoring).

### 15.2 UI Auto Scaling (optional)

```bash
aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --resource-id "service/${ECS_CLUSTER}/rag-ui" \
  --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 1 \
  --max-capacity 4 \
  --role-name ecsAutoscaleRole

aws application-autoscaling put-scaling-policy \
  --service-namespace ecs \
  --resource-id "service/${ECS_CLUSTER}/rag-ui" \
  --scalable-dimension ecs:service:DesiredCount \
  --policy-name rag-ui-cpu \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration '{
    "PredefinedMetricSpecification": {"PredefinedMetricType": "ECSServiceAverageCPUUtilization"},
    "TargetValue": 70.0,
    "ScaleOutCooldown": 60,
    "ScaleInCooldown": 300
  }'
```

---

## 16. GitHub Secrets

Install the GitHub CLI and authenticate:

```bash
gh auth login
```

Set the repository. Replace `your-org` and `your-repo`:

```bash
gh repo set-default your-org/your-repo
```

Run these commands to create the secrets:

```bash
gh secret set AWS_ACCESS_KEY_ID        --body "YOUR_AWS_ACCESS_KEY_ID"
gh secret set AWS_SECRET_ACCESS_KEY    --body "YOUR_AWS_SECRET_ACCESS_KEY"
gh secret set AWS_REGION               --body "$AWS_REGION"
gh secret set ECR_REPOSITORY           --body "$ECR_REPO"
gh secret set ECS_CLUSTER              --body "$ECS_CLUSTER"
gh secret set ECS_SERVICE_API          --body "rag-api"
gh secret set ECS_SERVICE_UI           --body "rag-ui"

gh secret set NEON_DB_URL_ARN             --body "$NEON_DB_URL_ARN"
gh secret set UPSTASH_REDIS_REST_URL_ARN  --body "$UPSTASH_REDIS_REST_URL_ARN"
gh secret set UPSTASH_REDIS_REST_TOKEN_ARN --body "$UPSTASH_REDIS_REST_TOKEN_ARN"
gh secret set QDRANT_URL_ARN             --body "$QDRANT_URL_ARN"
gh secret set QDRANT_API_KEY_ARN       --body "$QDRANT_API_KEY_ARN"
gh secret set OPENAI_API_KEY_ARN       --body "$OPENAI_API_KEY_ARN"
gh secret set JINA_API_KEY_ARN         --body "$JINA_API_KEY_ARN"
gh secret set PORTKEY_API_KEY_ARN      --body "$PORTKEY_API_KEY_ARN"
gh secret set RAG_API_KEY_ARN          --body "$RAG_API_KEY_ARN"
gh secret set LOGFIRE_TOKEN_ARN        --body "$LOGFIRE_TOKEN_ARN"
gh secret set LANGSMITH_API_KEY_ARN    --body "$LANGSMITH_API_KEY_ARN"
```

Verify the secrets were created:

```bash
gh secret list
```

---

## 17. Push & Trigger CI/CD

From the project root, on the `deployment` branch:

```bash
git checkout deployment
git push origin deployment
```

In GitHub:

1. Go to **Actions** → **CI** workflow.
2. Confirm the CI run on the `deployment` branch succeeds (lint + tests).
3. After CI succeeds, the **CD** workflow will trigger automatically.
4. Monitor the CD workflow for build, push, and ECS deployment steps.

You can also watch ECS services from the CLI:

```bash
aws ecs describe-services \
  --cluster $ECS_CLUSTER \
  --services rag-api rag-ui
```

---

## 18. Validation

Once services are stable, test the deployment:

```bash
export API_URL="http://${ALB_DNS}"

# Health
curl -s "${API_URL}/health"

# Readiness
curl -s "${API_URL}/ready"

# Submit a query
curl -s -X POST "${API_URL}/query" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-production-api-key" \
  -d '{"q":"What is a Kubernetes pod?","thread_id":"aws-test"}'

# UI
echo "Open http://${ALB_DNS}/ui"
```

Check logs:

```bash
aws logs tail /ecs/rag-api --follow
```

---

## 19. Cleanup

Run these commands in order to avoid dependency errors.

```bash
# 1. Delete ECS services
aws ecs update-service --cluster $ECS_CLUSTER --service rag-api --desired-count 0
aws ecs update-service --cluster $ECS_CLUSTER --service rag-ui --desired-count 0

aws ecs delete-service --cluster $ECS_CLUSTER --service rag-api --force
aws ecs delete-service --cluster $ECS_CLUSTER --service rag-ui --force

# 2. Deregister task definitions (mark inactive)
for FAMILY in rag-api rag-ui; do
  aws ecs deregister-task-definition --task-definition "${FAMILY}:1"
done

# 3. Delete ALB, listeners, target groups
aws elbv2 delete-load-balancer --load-balancer-arn $ALB_ARN
aws elbv2 delete-target-group --target-group-arn $API_TG_ARN
aws elbv2 delete-target-group --target-group-arn $UI_TG_ARN

# 4. Delete ECS cluster
aws ecs delete-cluster --cluster $ECS_CLUSTER

# 5. Delete ECR repository
aws ecr delete-repository --repository-name $ECR_REPO --force

# 6. Delete Secrets Manager secrets
for NAME in neon-db-url upstash-redis-rest-url upstash-redis-rest-token qdrant-url qdrant-api-key openai-api-key jina-api-key portkey-api-key rag-api-key logfire-token langsmith-api-key; do
  aws secretsmanager delete-secret --secret-id "${PROJECT}/${NAME}" --force-delete-without-recovery
done

# 7. Delete IAM policy and roles
aws iam detach-role-policy --role-name rag-api-task-role --policy-arn $SECRETS_POLICY_ARN
aws iam detach-role-policy --role-name rag-ui-task-role --policy-arn $SECRETS_POLICY_ARN
aws iam delete-policy --policy-arn $SECRETS_POLICY_ARN

for ROLE in rag-api-task-role rag-ui-task-role; do
  aws iam delete-role --role-name $ROLE
done

# 8. Delete CloudWatch log groups
aws logs delete-log-group --log-group-name /ecs/rag-api
aws logs delete-log-group --log-group-name /ecs/rag-ui

# 9. Delete NAT gateway and Elastic IP
aws ec2 delete-nat-gateway --nat-gateway-id $NAT_GW_1
aws ec2 release-address --allocation-id $EIP_1

# 10. Delete route tables, subnets, IGW, VPC
aws ec2 delete-route-table --route-table-id $PUBLIC_RT
aws ec2 delete-route-table --route-table-id $PRIVATE_RT

aws ec2 delete-subnet --subnet-id $PUBLIC_SUBNET_1
aws ec2 delete-subnet --subnet-id $PUBLIC_SUBNET_2
aws ec2 delete-subnet --subnet-id $PRIVATE_SUBNET_1
aws ec2 delete-subnet --subnet-id $PRIVATE_SUBNET_2

aws ec2 detach-internet-gateway --internet-gateway-id $IGW_ID --vpc-id $VPC_ID
aws ec2 delete-internet-gateway --internet-gateway-id $IGW_ID

aws ec2 delete-vpc --vpc-id $VPC_ID
```

> **Warning:** Cleanup deletes AWS resources. Neon and Upstash databases must be deleted separately in their respective consoles if you no longer need them.
