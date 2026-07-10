#!/usr/bin/env bash
# AWS deployment environment variables
export AWS_REGION="us-east-1"
export PROJECT="rag"
export VPC_CIDR="10.0.0.0/16"
export PUBLIC_SUBNET_1_CIDR="10.0.1.0/24"
export PUBLIC_SUBNET_2_CIDR="10.0.2.0/24"
export PRIVATE_SUBNET_1_CIDR="10.0.3.0/24"
export PRIVATE_SUBNET_2_CIDR="10.0.4.0/24"

export VPC_NAME="${PROJECT}-vpc"
export ALB_NAME="${PROJECT}-alb"
export ECR_REPO="enterprise-rag"
export ECS_CLUSTER="${PROJECT}-cluster"
