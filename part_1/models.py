from django.db import models
from django.utils import timezone


class Experiment(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        RUNNING = "running", "Running"
        FINISHED = "finished", "Finished"
        ABORTED = "aborted", "Aborted"
        FAILED = "failed", "Failed"

    # Коротка назва/мітка, щоб відрізняти експерименти в UI/адмінці.
    title = models.CharField(max_length=200, default="Untitled")

    # Довільний опис/метадані.
    description = models.TextField(blank=True)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    # Використовуємо default=timezone.now замість auto_now_add/auto_now, щоб міграції
    # не вимагали інтерактивного вводу для існуючих рядків у БД.
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)

    # Часові точки на стороні сервера (можуть не збігатися з t=0 на мікроконтролері).
    started_at = models.DateTimeField(null=True, blank=True)
    ignited_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # Мінімальні поля конфігурації під Raspberry Pi <-> Arduino.
    serial_port = models.CharField(max_length=128, blank=True, default="")
    baud_rate = models.PositiveIntegerField(default=115200)

    class Meta:
        # Проєкт вже має міграції з опечаткою Experement, тому тримаємо існуючу назву таблиці.
        # Це дозволяє перейменувати клас в Python без "перетягування" таблиці в БД.
        db_table = "part_1_experement"
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.status})"

    def save(self, *args, **kwargs):
        self.updated_at = timezone.now()
        return super().save(*args, **kwargs)


class Frame(models.Model):
    """
    Один "кадр" вимірювань (time-sample) з експерименту.

    Поля зберігаються як float, тому важливо домовитись про сталі одиниці виміру
    на рівні API/клієнта (наприклад: секунди, градуси, Па/бар тощо).
    """

    # Кадр належить рівно одному експерименту.
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="frames",
        null=True,
        blank=True,
    )

    # Часова мітка/зміщення від початку експерименту (в секундах).
    second = models.FloatField()

    # Показник температури (одиниці визначаються контрактом даних/пристроєм).
    temperature = models.FloatField()

    # Диференціальний тиск (pressure delta; одиниці визначаються контрактом даних/пристроєм).
    dif_pressure = models.FloatField()

    received_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        indexes = [
            models.Index(fields=["experiment", "second"]),
        ]
        ordering = ["second", "id"]

    @classmethod
    def bulk_create_from_payload(cls, payload, *, experiment, batch_size=1000):
        """
        Приймає payload з API і масово створює записи Frame через bulk_create().

        Підтримувані формати:
        - {"frames": [{...}, {...}]}
        - [{...}, {...}]

        Важливо:
        - bulk_create() не викликає model.save(), не запускає сигнали і не робить full_clean().
        - Значення приводяться до float; при некоректних даних кидається ValueError.
        """
        if experiment is None:
            raise ValueError("Experiment is required.")

        if isinstance(payload, dict):
            items = payload.get("frames")
        else:
            items = payload

        if not isinstance(items, list) or not items:
            raise ValueError("Payload must contain a non-empty list of frames.")

        frames = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"Frame at index {idx} must be an object.")

            required = ("second", "temperature", "dif_pressure")
            missing = [field for field in required if field not in item]
            if missing:
                raise ValueError(
                    f"Frame at index {idx} missing required fields: {', '.join(missing)}."
                )

            try:
                frame = cls(
                    experiment=experiment,
                    second=float(item["second"]),
                    temperature=float(item["temperature"]),
                    dif_pressure=float(item["dif_pressure"]),
                )
            except (TypeError, ValueError):
                raise ValueError(
                    f"Frame at index {idx} has invalid numeric values."
                ) from None

            frames.append(frame)

        return cls.objects.bulk_create(frames, batch_size=batch_size)

    def __str__(self):
        # Людиночитний рядок для адмінки/логів.
        return f"Second: {self.second}, Temperature: {self.temperature}, Dif Pressure: {self.dif_pressure}"
