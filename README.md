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

Сервіс слухає `:8000`. На проді зазвичай ставлять nginx/Traefik/Cloudflare Tunnel перед ним.

Під час старту контейнер автоматично виконує `migrate` + `collectstatic`.

## Cloudflare Tunnel (cloudflared) на Raspberry Pi
Цей репозиторій вже містить `docker-compose.yml` з сервісом `cloudflared`.

1. У Cloudflare Zero Trust (Dashboard) створи тунель: `Networks -> Tunnels -> Create tunnel` і вибери варіант Docker.
2. Скопіюй `TUNNEL_TOKEN` і додай в `.env`:
   - `TUNNEL_TOKEN=...`
3. Додай Public Hostname для тунеля і вкажи service/origin URL:
   - `http://web:8000`
4. В `.env` вистав домен для Django:
   - `DJANGO_ALLOWED_HOSTS=yourdomain.com`
   - `DJANGO_CSRF_TRUSTED_ORIGINS=https://yourdomain.com`
5. За потреби (якщо хочеш форсити HTTPS на рівні Django):
   - `DJANGO_SECURE_SSL_REDIRECT=1`
   - `DJANGO_SESSION_COOKIE_SECURE=1`
   - `DJANGO_CSRF_COOKIE_SECURE=1`

Примітка: в `docker-compose.yml` порт `8000` за замовчуванням проброшений тільки на `127.0.0.1`, щоб не світити сервіс у LAN/Internet.

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
