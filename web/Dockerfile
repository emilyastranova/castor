# The build, and installing the runtime packages, run on the builder's own CPU
# ($BUILDPLATFORM), never under emulation: the output (the adapter-node build and plain-JS
# packages, none native) is the same on every architecture, so an arm64 image only copies it.
FROM --platform=$BUILDPLATFORM docker.io/library/node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM --platform=$BUILDPLATFORM docker.io/library/node:22-alpine AS runtime-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM docker.io/library/node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/build ./build
COPY --from=runtime-deps /app/node_modules ./node_modules
COPY package.json ./
EXPOSE 3000
CMD ["node", "build"]
