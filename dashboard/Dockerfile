FROM node:20-alpine

WORKDIR /app

# Copy package definition and install dependencies
COPY dashboard/package.json ./
RUN npm install

# Copy application sources
COPY dashboard/ ./

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
