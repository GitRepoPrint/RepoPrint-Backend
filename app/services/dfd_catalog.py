# Mapeia o nome de um módulo importado para o tipo de elemento externo que ele representa num DFD.
# A correspondência é por prefixo, sempre seguida de um separador (., /, :), por isso "requests"
# apanha "requests.adapters" mas não "requests_oauthlib".

DATASTORE= "datastore"
EXTERNAL= "external"
QUEUE= "queue"
ENTRYPOINT= "entrypoint"

CATALOG: dict[str, tuple[str, str]]= {}


def _add(kind: str, label: str, *modules: str):
    for module in modules:
        CATALOG[module.lower()]= (kind, label)


_add(DATASTORE, "PostgreSQL", "psycopg2", "psycopg", "asyncpg", "pg", "pg-promise", "lib/pq", "github.com/lib/pq", "github.com/jackc/pgx", "tokio_postgres", "org.postgresql", "npgsql")
_add(DATASTORE, "MySQL", "mysql", "mysql2", "pymysql", "mysqli", "mysql.connector", "github.com/go-sql-driver/mysql", "mysql_async", "com.mysql")
_add(DATASTORE, "SQLite", "sqlite3", "better-sqlite3", "rusqlite", "mattn/go-sqlite3", "sqlite")
_add(DATASTORE, "MongoDB", "pymongo", "motor", "mongoose", "mongodb", "go.mongodb.org/mongo-driver", "mongo", "org.mongodb")
_add(DATASTORE, "Redis", "redis", "ioredis", "aioredis", "github.com/redis/go-redis", "github.com/go-redis/redis", "redis-py", "jedis", "org.redisson", "stackexchange.redis")
_add(DATASTORE, "SQL database", "sqlalchemy", "django.db", "peewee", "tortoise", "sqlmodel", "databases", "prisma", "@prisma/client", "typeorm", "sequelize", "knex", "drizzle-orm", "java.sql", "javax.sql", "javax.persistence", "jakarta.persistence", "org.hibernate", "org.springframework.data", "org.springframework.jdbc", "database/sql", "gorm.io/gorm", "github.com/jmoiron/sqlx", "diesel", "sqlx", "sea_orm", "activerecord", "active_record", "sequel", "pdo", "doctrine", "illuminate.database", "microsoft.entityframeworkcore", "system.data", "dapper", "slick", "exposed")
_add(DATASTORE, "Elasticsearch", "elasticsearch", "@elastic/elasticsearch", "org.elasticsearch", "opensearch", "opensearchpy")
_add(DATASTORE, "Cassandra", "cassandra", "gocql", "com.datastax")
_add(DATASTORE, "Firebase", "firebase", "firebase_admin", "firebase-admin", "@firebase", "cloud.google.com/go/firestore", "com.google.firebase")
_add(DATASTORE, "File storage", "fs", "fs.promises", "pathlib", "shutil", "java.nio.file", "java.io", "std::fs", "os.readfile", "io/ioutil")

_add(EXTERNAL, "HTTP API", "requests", "httpx", "aiohttp", "urllib", "urllib3", "http.client", "axios", "node-fetch", "got", "superagent", "undici", "ky", "net/http", "reqwest", "hyper", "ureq", "surf", "okhttp3", "okhttp", "java.net.http", "org.apache.http", "org.apache.hc", "retrofit2", "org.springframework.web.client", "org.springframework.web.reactive.function.client", "system.net.http", "net/http", "faraday", "httparty", "rest-client", "curl", "guzzlehttp", "symfony.component.httpclient", "dio", "package:http", "alamofire", "urlsession")
_add(EXTERNAL, "AWS", "boto3", "botocore", "aws-sdk", "@aws-sdk", "software.amazon.awssdk", "com.amazonaws", "github.com/aws/aws-sdk-go", "aws_sdk", "aws-sdk-go-v2")
_add(EXTERNAL, "Google Cloud", "google.cloud", "@google-cloud", "cloud.google.com/go", "com.google.cloud", "googleapiclient", "gcloud")
_add(EXTERNAL, "Azure", "azure", "@azure", "com.azure", "microsoft.azure", "azure-sdk-for-go")
_add(EXTERNAL, "Stripe", "stripe", "com.stripe", "stripe-go", "github.com/stripe")
_add(EXTERNAL, "Twilio", "twilio", "com.twilio")
_add(EXTERNAL, "SendGrid / SMTP", "sendgrid", "@sendgrid", "smtplib", "nodemailer", "javax.mail", "jakarta.mail", "net/smtp", "lettre", "mail", "actionmailer")
_add(EXTERNAL, "OpenAI", "openai", "github.com/sashabaranov/go-openai", "async-openai")
_add(EXTERNAL, "Anthropic", "anthropic", "@anthropic-ai/sdk")
_add(EXTERNAL, "Sentry", "sentry_sdk", "@sentry", "io.sentry", "sentry")
_add(EXTERNAL, "Auth (OAuth / JWT)", "keycloak", "keycloak-js", "org.keycloak", "authlib", "oauthlib", "passport", "passport-oauth2", "golang.org/x/oauth2", "jsonwebtoken", "jwt", "pyjwt")
_add(EXTERNAL, "WebSocket", "websockets", "websocket", "ws", "socket.io", "socket.io-client", "gorilla/websocket", "github.com/gorilla/websocket", "tungstenite", "tokio_tungstenite")
_add(EXTERNAL, "gRPC", "grpc", "@grpc/grpc-js", "io.grpc", "google.golang.org/grpc", "tonic")

_add(QUEUE, "Kafka", "kafka", "kafkajs", "confluent_kafka", "kafka-python", "org.apache.kafka", "org.springframework.kafka", "github.com/segmentio/kafka-go", "github.com/shopify/sarama", "rdkafka")
_add(QUEUE, "RabbitMQ", "pika", "aio_pika", "amqplib", "amqp", "com.rabbitmq", "org.springframework.amqp", "github.com/streadway/amqp", "github.com/rabbitmq/amqp091-go", "lapin", "bunny")
_add(QUEUE, "Message queue", "celery", "rq", "bullmq", "bull", "kue", "nats", "github.com/nats-io/nats.go", "sidekiq", "resque", "javax.jms", "jakarta.jms", "paho.mqtt", "mqtt", "azure.servicebus", "pubsub")

_add(ENTRYPOINT, "Web API", "litestar", "koa-router", "@koa/router", "restify", "polka", "express-promise-router", "flask", "fastapi", "django.http", "django.views", "django.urls", "rest_framework", "starlette", "tornado", "aiohttp.web", "bottle", "falcon", "sanic", "express", "koa", "fastify", "@nestjs", "hapi", "@hapi", "next", "next/server", "hono", "org.springframework.web", "org.springframework.web.bind", "javax.ws.rs", "jakarta.ws.rs", "io.micronaut", "io.quarkus", "io.javalin", "play.mvc", "github.com/gin-gonic/gin", "github.com/labstack/echo", "github.com/gofiber/fiber", "github.com/gorilla/mux", "github.com/go-chi/chi", "actix_web", "axum", "rocket", "warp", "tide", "rails", "action_controller", "sinatra", "grape", "illuminate.http", "illuminate.routing", "symfony.component.httpfoundation", "slim", "microsoft.aspnetcore", "vapor", "ktor", "io.ktor", "phoenix", "plug")


def classify(module: str) -> tuple[str, str] | None:
    lowered= module.lower()
    for key, value in CATALOG.items():
        if lowered == key or (lowered.startswith(key) and lowered[len(key)] in "./:\\"):
            return value
    return None
