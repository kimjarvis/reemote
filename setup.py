from setuptools import setup, find_packages

setup(
    name="reemote",  # Name of your package
    version="0.0.1",   # Version number
    description="A Python package for reemote functionality",  # Short description
    long_description="""
    Reemote is a Python API for task automation, configuration management and application deployment.
    """,
    long_description_content_type="text/markdown",  # Change to "text/x-rst" if using reStructuredText
    author="Kim Jarvis",  # Your name
    author_email="kim.jarvis@tpfsystems.com",  # Your email
    url="https://github.com/kimjarvis/reemote",  # URL to the source code repository
    license="MIT",  # License type
    packages=find_packages(),  # Automatically find all packages in the directory
    install_requires=[
        "cryptography",
        "bcrypt",
        "asyncssh",
        "tabulate"
    ],  # List of dependencies with proper commas
    extras_require={
        "dev": [
            "setuptools",
            "asyncssh",
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
            'reemote=reemote_cli.reemote:main',  # This creates a CLI command `reemote`
        ],
    },
)