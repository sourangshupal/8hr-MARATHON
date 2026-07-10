#!/usr/bin/env bash
# Destroy all AWS resources created during the deployment.
# Run from the repo root: bash scripts/destroy_aws_deployment.sh
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/aws_deploy_env.sh
source scripts/aws_deploy_state.sh

echo "=== AWS resource teardown starting ==="

# 1. Application Auto Scaling
# Deregister scalable targets first (deletes policies automatically if no targets remain).
aws application-autoscaling deregister-scalable-target \
  --service-namespace ecs \
  --resource-id service/${ECS_CLUSTER}/rag-api \
  --scalable-dimension ecs:service:DesiredCount 2>/dev/null || true
aws application-autoscaling deregister-scalable-target \
  --service-namespace ecs \
  --resource-id service/${ECS_CLUSTER}/rag-ui \
  --scalable-dimension ecs:service:DesiredCount 2>/dev/null || true

# 2. Stop and delete ECS services
aws ecs update-service --cluster $ECS_CLUSTER --service rag-api --desired-count 0 || true
aws ecs update-service --cluster $ECS_CLUSTER --service rag-ui --desired-count 0 || true
sleep 30
aws ecs delete-service --cluster $ECS_CLUSTER --service rag-api --force || true
aws ecs delete-service --cluster $ECS_CLUSTER --service rag-ui --force || true

# Wait until no services remain
until [ "$(aws ecs list-services --cluster $ECS_CLUSTER --query 'serviceArns' --output text | wc -w)" -eq 0 ]; do
  echo "Waiting for ECS services to delete..."
  sleep 15
done

# 3. Deregister all task definition revisions for rag-api and rag-ui
for FAMILY in rag-api rag-ui; do
  ARNS=$(aws ecs list-task-definitions --family-prefix $FAMILY --status ACTIVE --query 'taskDefinitionArns' --output text)
  for ARN in $ARNS; do
    aws ecs deregister-task-definition --task-definition $ARN || true
  done
done

# 4. Delete ALB, listeners, target groups
aws elbv2 delete-load-balancer --load-balancer-arn $ALB_ARN || true
sleep 15
aws elbv2 delete-listener --listener-arn $LISTENER_ARN 2>/dev/null || true
aws elbv2 delete-target-group --target-group-arn $API_TG_ARN || true
aws elbv2 delete-target-group --target-group-arn $UI_TG_ARN || true

# 5. Delete ECS cluster
aws ecs delete-cluster --cluster $ECS_CLUSTER || true

# 6. Delete ECR repository
aws ecr delete-repository --repository-name $ECR_REPO --force || true

# 7. Delete Secrets Manager secrets
for NAME in neon-db-url upstash-redis-rest-url upstash-redis-rest-token qdrant-url qdrant-api-key openai-api-key jina-api-key portkey-api-key rag-api-key logfire-token langsmith-api-key; do
  aws secretsmanager delete-secret --secret-id "${PROJECT}/${NAME}" --force-delete-without-recovery || true
done

# 8. Detach IAM policies and delete custom roles/policy
aws iam detach-role-policy --role-name rag-api-task-role --policy-arn $SECRETS_POLICY_ARN || true
aws iam detach-role-policy --role-name rag-ui-task-role --policy-arn $SECRETS_POLICY_ARN || true
aws iam detach-role-policy --role-name ecsTaskExecutionRole --policy-arn $SECRETS_POLICY_ARN || true
aws iam delete-policy --policy-arn $SECRETS_POLICY_ARN || true

for ROLE in rag-api-task-role rag-ui-task-role; do
  aws iam delete-role --role-name $ROLE || true
done

# 9. Delete CloudWatch log groups
aws logs delete-log-group --log-group-name /ecs/rag-api || true
aws logs delete-log-group --log-group-name /ecs/rag-ui || true

# 10. Delete NAT gateway and wait for it to be gone, then release EIP
aws ec2 delete-nat-gateway --nat-gateway-id $NAT_GW_1 || true
until [ "$(aws ec2 describe-nat-gateways --nat-gateway-ids $NAT_GW_1 --query 'NatGateways[0].State' --output text 2>/dev/null)" != "deleting" ]; do
  echo "Waiting for NAT gateway to delete..."
  sleep 20
done
aws ec2 release-address --allocation-id $EIP_1 || true

# 11. Delete route table associations, then route tables
# Public route table
for ASSOC in $(aws ec2 describe-route-tables --route-table-ids $PUBLIC_RT --query 'RouteTables[0].Associations[].RouteTableAssociationId' --output text 2>/dev/null); do
  aws ec2 disassociate-route-table --association-id $ASSOC || true
done
# Private route table
for ASSOC in $(aws ec2 describe-route-tables --route-table-ids $PRIVATE_RT --query 'RouteTables[0].Associations[].RouteTableAssociationId' --output text 2>/dev/null); do
  aws ec2 disassociate-route-table --association-id $ASSOC || true
done
aws ec2 delete-route-table --route-table-id $PUBLIC_RT || true
aws ec2 delete-route-table --route-table-id $PRIVATE_RT || true

# 12. Delete subnets
aws ec2 delete-subnet --subnet-id $PUBLIC_SUBNET_1 || true
aws ec2 delete-subnet --subnet-id $PUBLIC_SUBNET_2 || true
aws ec2 delete-subnet --subnet-id $PRIVATE_SUBNET_1 || true
aws ec2 delete-subnet --subnet-id $PRIVATE_SUBNET_2 || true

# 13. Detach and delete IGW
aws ec2 detach-internet-gateway --internet-gateway-id $IGW_ID --vpc-id $VPC_ID || true
aws ec2 delete-internet-gateway --internet-gateway-id $IGW_ID || true

# 14. Delete security groups (need to retry if dependencies remain)
for SG in $UI_SG $API_SG $ALB_SG; do
  until aws ec2 delete-security-group --group-id $SG 2>/dev/null; do
    echo "Waiting to delete security group $SG..."
    sleep 10
  done || true
done

# 15. Delete VPC
aws ec2 delete-vpc --vpc-id $VPC_ID || true

echo "=== AWS resource teardown complete ==="
