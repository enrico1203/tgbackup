FROM node:26-alpine AS builder

WORKDIR /app
# npm ci and not npm install: it installs exactly the lockfile, so the image built today
# and the one built in six months contain the same dependency tree.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build


FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
