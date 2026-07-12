#!/bin/bash
# Script to easily stage, commit, and push all modifications to GitHub

# Default commit message if none provided
COMMIT_MSG=${1:-"Update: strategy adjustments and tracking utilities"}

echo "=== Git Push Automation ==="
echo "Staging all changes..."
git add -A

echo "Committing changes..."
git commit -m "$COMMIT_MSG"

echo "Pushing to remote repository..."
git push origin main

echo "=== Push Complete ==="
