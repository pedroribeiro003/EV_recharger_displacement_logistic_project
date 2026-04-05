#!/bin/sh
set -e

echo "Waiting for database..."
until python -c "
import psycopg2, os, sys
try:
    psycopg2.connect(os.environ['DATABASE_URL'].replace('+psycopg2',''))
    print('DB ready.')
except Exception as e:
    print(f'Not ready: {e}')
    sys.exit(1)
" 2>/dev/null; do
  sleep 2
done

echo "Running migrations..."
alembic upgrade head

echo "Starting: $@"
exec "$@"
