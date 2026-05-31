# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies using yarn lockfile for reproducibility
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile

# Copy source and build
COPY . .
RUN yarn build

# Stage 2: Serve with nginx
FROM nginx:1.25-alpine

# Copy built assets
COPY --from=builder /app/build /usr/share/nginx/html

# Copy nginx config (handles SPA routing and API proxying)
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
