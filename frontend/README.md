# JobPilot Frontend

This directory contains the Svelte frontend for JobPilot.

## Requirements

- Node.js 18+
- npm

## Install dependencies

```sh
npm install
```

From the repository root, the equivalent command is:

```sh
npm install --prefix frontend
```

## Run in development

```sh
npm run dev
```

By default the app expects the backend to be available locally. If you need to point the frontend at a different backend, configure `VITE_API_BASE_URL`.

## Type-check

```sh
npm run check
```

## Build

```sh
npm run build
```

From the repository root:

```sh
npm run build --prefix frontend
```

The production build is written to `frontend/build` and served by the backend when available.
