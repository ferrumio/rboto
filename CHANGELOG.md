# Changelog

All notable changes to this project will be documented in this file.

This file is generated from conventional commit messages by
[git-cliff](https://git-cliff.org/) after each release. See `cliff.toml`.

## [Unreleased]

### Features

- Generated async, strictly typed clients for Amazon S3, SNS, SQS, and DynamoDB, powered
  by the AWS SDK for Rust.
- One native wheel per service with a pure-Python `rboto` facade providing `Session`,
  shared exceptions, and service factories.
- Streaming S3 response bodies exposed as Python async iterators.
- Modeled AWS errors raised as generated Python exception subclasses.
