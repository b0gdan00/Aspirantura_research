# asp_experiment (Django) деплой через Docker

## Запуск на Linux сервері
1. Створи `.env` на базі `.env.example` і задай мінімум:
   - `DJANGO_SECRET_KEY`
   - `DJANGO_ALLOWED_HOSTS` (домен/IPv4, через кому)
   - `DJANGO_DEBUG=0`
2. Запусти:
```sh
docker compose up -d --build
```

Сервіс слухає `:8000`. На проді зазвичай ставлять nginx/Traefik перед ним.

Під час старту контейнер автоматично виконує `migrate` + `collectstatic`.

## Адмін (superuser)
```sh
docker compose exec web python manage.py createsuperuser
```

## SQLite (збереження даних)
База лежить на хості в `./data` (див. `docker-compose.yml` + `DJANGO_SQLITE_PATH`).

## Serial (опційно)
Якщо потрібен доступ `pyserial`, прокинь пристрій у контейнер, наприклад:
```yaml
services:
  web:
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
```
