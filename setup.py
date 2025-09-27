from setuptools import setup, find_packages

setup(
    name="reemote",  # Name of your package
    version="0.0.13",   # Version number
    description="A Python package for reemote functionality",  # Short description
    long_description="""
    Reemote is a Python API for task automation, configuration management and application deployment.
    """,
    long_description_content_type="text/markdown",  # Change to "text/x-rst" if using reStructuredText
    author="Kim Jarvis",  # Your name
    author_email="kim.jarvis@tpfsystems.com",  # Your email
    url="http://reemote.org/",
    project_urls={
        "Documentation": "https://reemote.org/",
        "Source Code": "https://github.com/kimjarvis/reemote",
        "Bug Tracker": "https://github.com/kimjarvis/reemote/issues",
        "Changelog": "https://github.com/kimjarvis/reemote/blob/main/CHANGELOG.md",
    },
    license="MIT",  # License type
    packages=find_packages(),  # Automatically find all packages in the directory
    install_requires=[
        "cryptography",
        "bcrypt",
        "asyncssh",
        "tabulate",
        "fastapi",
        "nicegui",
    ],  # List of dependencies with proper commas
    extras_require={
        "dev": [
            "setuptools",
        ],
        "doc": [
            "sphinx",
        ],
        "test": [
            "pytest",
        ],
    },
    python_requires=">=3.6",  # Specify the minimum Python version required
    entry_points={
        'console_scripts': [
            'reemote=reemote.cli:_main',  # Use the synchronous wrapper
            'reemotecontrol=reemote.gui.main:_main'
        ],
    },
)
