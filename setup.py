from setuptools import setup

setup(
    name="onql-client",
    version="0.1.1",
    description="ONQL Python client",
    author="Paras Virk",
    author_email="team@autobit.co",
    packages=["onqlclient"],
    python_requires=">=3.7",
    license="MIT",
    url="https://onql.org",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
)
