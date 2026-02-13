from django.test import TestCase
from django.urls import reverse

from .models import Experiment, Frame


class FrameBatchIngestTests(TestCase):
    def test_bulk_create_from_list_payload(self):
        experiment = Experiment.objects.create(title="Test experiment")

        payload = [
            {"second": 1, "temperature": 20.5, "dif_pressure": 0.1},
            {"second": 2, "temperature": 21.5, "dif_pressure": 0.2},
        ]

        created = Frame.bulk_create_from_payload(payload, experiment=experiment)

        self.assertEqual(len(created), 2)
        self.assertEqual(Frame.objects.count(), 2)

    def test_api_accepts_wrapped_payload(self):
        experiment = Experiment.objects.create(title="Test experiment")

        payload = {
            "frames": [
                {"second": 1, "temperature": 20.5, "dif_pressure": 0.1},
                {"second": 2, "temperature": 21.5, "dif_pressure": 0.2},
            ]
        }

        response = self.client.post(
            reverse("frame_batch_ingest", kwargs={"experiment_id": experiment.id}),
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["created"], 2)
        self.assertEqual(Frame.objects.count(), 2)

    def test_api_rejects_invalid_payload(self):
        experiment = Experiment.objects.create(title="Test experiment")

        payload = {"frames": [{"second": 1, "temperature": "bad"}]}

        response = self.client.post(
            reverse("frame_batch_ingest", kwargs={"experiment_id": experiment.id}),
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Frame.objects.count(), 0)
