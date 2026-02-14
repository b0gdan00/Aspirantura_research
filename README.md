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

## Arduino sketch (Serial protocol)
Скетч для Arduino лежить в `arduino/asp_experiment_controller/asp_experiment_controller.ino`.
Він підтримує команди: `PING`, `START`, `STOP`, `READ_ALL` (див. коментарі в скетчі).

## Raspberry Pi collector (максимальна частота опитування)
Щоб не відкривати Serial-порт на кожен запит (це повільно), телеметрію краще збирати окремим процесом.
У репозиторії є скрипт `scripts/pi_collector.py`, який:
- тримає Serial відкритим
- опитує Arduino командою `READ_ALL` з частотою `POLL_HZ`
- відправляє кадри batch'ами на `/api/experiments/<id>/frames/batch/`

Варіант через Docker Compose:
1. Заповни в `.env` (або задай змінні оточення):
   - `EXPERIMENT_ID=...`
   - `SERIAL_PORT=/dev/ttyUSB0` (або задай serial_port у UI; тоді можна не вказувати тут)
2. Запусти web + collector:
```sh
docker compose --profile collector up -d --build
```
