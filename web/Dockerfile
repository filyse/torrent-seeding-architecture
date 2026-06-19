FROM node:22-alpine AS build
WORKDIR /app
COPY web/package.json ./
RUN npm install
COPY web/ ./
RUN npm run build
# Метка времени сборки — отдаётся статикой по /BUILD_TIME.
RUN date -u +%Y-%m-%dT%H:%M:%SZ > /app/dist/BUILD_TIME

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
