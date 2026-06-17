FROM node:20-alpine

WORKDIR /app

COPY . .
RUN npm install @rollup/rollup-linux-arm64-musl @esbuild/linux-arm64 --legacy-peer-deps

EXPOSE 5173

# Run the dev server listening on 0.0.0.0
CMD ["npm", "run", "dev", "--", "--host"]
