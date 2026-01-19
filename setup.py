"""Setup script for TDL wrapper."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="tdl-wrapper",
    version="1.0.0",
    author="Your Name",
    description="Advanced wrapper for tdl (Telegram Downloader) with incremental sync, scheduling, and monitoring",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/tdl-wrapper",
    packages=find_packages(),
    package_data={
        'src.web': ['templates/*.html', 'static/*'],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Communications :: Chat",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "tdl-wrapper=src.cli:main",
        ],
    },
)
