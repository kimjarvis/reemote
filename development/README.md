# How to Contribute

## Fork the Repository
Start by forking this repository to your own GitHub account. Click the "Fork" button at the top-right corner of this page . This will create a copy of the project under your account.
Clone the Forked Repository
Once forked, clone the repository to your local machine using the following command:

## Clone the Forked Repository 
```
git clone git@github.com:YOUR_GITHUB_USERNAME/reemote.git
```
Replace YOUR_GITHUB_USERNAME with your GitHub username.

## Create a New Branch
Before making changes, create a new branch for your work. This helps keep your changes organized and makes it easier to submit a pull request later.

```
git checkout -b feature/your-feature-name
```

Replace your-feature-name with a descriptive name for your feature or fix (e.g., fix-bug-chmod).
Make Your Changes
Make the necessary changes to the codebase. Ensure your changes align with the project's coding standards and guidelines. If you're unsure, feel free to open an issue to discuss your idea before implementing it.

## Commit Your Changes
Once you've made your changes, commit them with a clear and descriptive commit message:

```
git add .
git commit -m "Add a brief description of your changes"
```

## Push Your Changes to GitHub
Push your changes to your forked repository on GitHub:

```
git push origin feature/your-feature-name
```

Submit a Pull Request (PR)
Go to your forked repository on GitHub and click the "Compare & Pull Request" button. Ensure the base repository is set to the original project (kimjarvis/reemote) and the head repository is your forked branch (feature/your-feature-name).
Write a clear and concise description of your changes in the PR, explaining what you did and why. If your PR addresses an issue, mention the issue number (e.g., Fixes #123).

## Wait for Review
After submitting the PR, the maintainers of the project will review your changes. They may suggest improvements or ask for clarification. Be responsive to their feedback and make any necessary updates.
Merge
Once your PR is approved, the maintainer will merge it into the main project. 

## Development Setup

Install the development requirements.

```bash
# Create a virtualenv with your tool of choice
python3 -m venv venv_reemote
source venv_reemote/bin/activate

# Clone the repo
git clone git@github.com:kimjarvis/reemote.git
cd reemote

# Install the package in editable mode with development requirements
pip install -e './[dev]'
```

## Developing Examples

Each example has its own requirements.

```bash
pip install -r examples/gui/requirements.txt
python3 -m examples.gui.main.py
```

## Running Tests

Run all the tests before pushing.

```bash
pip install -e '.[test]'
python3 development/scripts/run_tests.py
```

## Generating the Documentation

Generate and view the autodoc documentation.

!this is under review!

```bash
pip install -e '.[doc]'
python3 scripts/build_documentation.py
open docs/_build/html/index.html
```
