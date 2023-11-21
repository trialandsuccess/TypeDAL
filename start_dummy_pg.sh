docker run -d -e POSTGRES_PASSWORD=somepass -e POSTGRES_USER=someuser -p 9631:5432 --name postgres-db postgres postgres

trap ctrl_c INT

function ctrl_c() {
    echo "Stopping and removing the PostgreSQL container..."
    docker stop postgres-db
    docker rm postgres-db
    exit 0
}

docker logs -f postgres-db
