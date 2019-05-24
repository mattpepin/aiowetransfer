import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="aiowetransfer",
    version="0.0.3",
    author="Francois Liot",
    author_email="francois@liot.org",
    maintainer="Matthieu Pepin",
    maintainer_email="matthieupepin@gmail.com",
    description="A Python 3 wrapper to use WeTransfer API V2 transfer and board",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/mattpepin/aiowetransfer",
    packages=setuptools.find_packages(),
    install_requires=[
        "aiohttp"
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Internet :: WWW/HTTP"
    ],
)
