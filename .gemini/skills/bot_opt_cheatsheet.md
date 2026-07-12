# Skill: Running Diagnostics & Optimization

When asked to optimize strategy parameters or check bot health, utilize the custom scripts in the repository:

1. **Trade Status Report**: Run `python3 /home/leeunks/.gemini/antigravity-cli/brain/785f9ef8-96d4-45b3-a549-213819137776/scratch/generate_report.py` to compile live PM2 status, state files, and recent metrics.
2. **Performance Analytics**: Run `python3 view_analytics.py` in the root of the project to analyze completed trades, fees, win rates, and asset performance.
3. **Fast Backtesting Grid Search**: Run `.venv/bin/python fast_optimize.py` to evaluate hundreds of parameter combinations in seconds. Ensure `--fee-rate 0.012` is configured to match the user's tier.

# Skill: GitHub Push Automation

Staging, committing, and pushing code is simplified:
* Run the helper script `./push.sh "[Commit message]"` to stage all local modifications, commit with the provided message (defaults to a general description if omitted), and push to the remote `main` branch.
