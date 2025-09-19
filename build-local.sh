#!/bin/bash

# Simple script to build the Docker image locally
# This will cache the vLLM base image and layers locally

set -e

echo "🐳 Building Docker image locally..."

cd container

docker build --platform linux/amd64 -t dotsocr:local .

echo "✅ Build complete! Image tagged as 'dotsocr:local'"
