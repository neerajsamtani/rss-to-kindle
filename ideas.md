# Ideas

## Features

- **Chrome Extension** — Instead of a CLI, make this a browser extension that non-technical users
  can configure.

## Reliability

- **Structured logging** — Replace `click.echo` with Python `logging` so poll mode is easier to
  diagnose.

## Infrastructure

- **Containerized poller** — The current Docker image serves the one-off web app; explore a
  separate way to run the RSS poller in a container.
