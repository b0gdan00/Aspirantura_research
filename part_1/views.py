import json
import threading

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from .telemetry import ensure_poller_running, get_session, stop_poller
from .forms import ExperimentCreateForm
from .models import Experiment, Frame


def empty_page(request):
    return redirect("experiments_list")


@require_GET
def experiments_list(request):
    experiments = Experiment.objects.order_by("-created_at")[:50]
    return render(
        request,
        "part_1/experiments_list.html",
        {"experiments": experiments},
    )


def experiment_create(request):
    if request.method == "POST":
        form = ExperimentCreateForm(request.POST)
        if form.is_valid():
            experiment = form.save()
            return redirect("experiment_detail", experiment_id=experiment.id)
    else:
        form = ExperimentCreateForm()

    return render(request, "part_1/experiment_create.html", {"form": form})


@require_GET
def experiment_detail(request, experiment_id: int):
    experiment = get_object_or_404(Experiment, pk=experiment_id)
    return render(
        request,
        "part_1/experiment_detail.html",
        {"experiment": experiment},
    )


@require_POST
def experiment_action(request, experiment_id: int):
    experiment = get_object_or_404(Experiment, pk=experiment_id)
    action = (request.POST.get("action") or "").strip().lower()
    now = timezone.now()

    if action == "start":
        if experiment.started_at is None:
            experiment.started_at = now
        experiment.status = Experiment.Status.RUNNING
        experiment.save()
        ensure_poller_running(experiment)
        return redirect("experiment_detail", experiment_id=experiment.id)

    if action == "ignite":
        if experiment.ignited_at is None:
            experiment.ignited_at = now
        if experiment.started_at is None:
            experiment.started_at = now
        experiment.status = Experiment.Status.RUNNING
        experiment.save()
        ensure_poller_running(experiment)
        return redirect("experiment_detail", experiment_id=experiment.id)

    if action == "finish":
        if experiment.ended_at is None:
            experiment.ended_at = now
        experiment.status = Experiment.Status.FINISHED
        experiment.save()
        stop_poller(experiment.id)
        return redirect("experiment_detail", experiment_id=experiment.id)

    if action == "abort":
        if experiment.ended_at is None:
            experiment.ended_at = now
        experiment.status = Experiment.Status.ABORTED
        experiment.save()
        stop_poller(experiment.id)
        return redirect("experiment_detail", experiment_id=experiment.id)

    return JsonResponse({"status": "error", "error": "Unknown action."}, status=400)


@require_GET
def experiment_summary_api(request, experiment_id: int):
    experiment = get_object_or_404(Experiment, pk=experiment_id)

    qs = Frame.objects.filter(experiment=experiment).order_by("-second", "-id")
    last = qs.first()

    return JsonResponse(
        {
            "status": "ok",
            "experiment": {
                "id": experiment.id,
                "title": experiment.title,
                "description": experiment.description,
                "status": experiment.status,
                "created_at": experiment.created_at.isoformat() if experiment.created_at else None,
                "updated_at": experiment.updated_at.isoformat() if experiment.updated_at else None,
                "started_at": experiment.started_at.isoformat() if experiment.started_at else None,
                "ignited_at": experiment.ignited_at.isoformat() if experiment.ignited_at else None,
                "ended_at": experiment.ended_at.isoformat() if experiment.ended_at else None,
                "serial_port": experiment.serial_port,
                "baud_rate": experiment.baud_rate,
            },
            "frames": {
                "count": Frame.objects.filter(experiment=experiment).count(),
                "last": (
                    {
                        "second": last.second,
                        "temperature": last.temperature,
                        "dif_pressure": last.dif_pressure,
                        "received_at": last.received_at.isoformat() if last.received_at else None,
                    }
                    if last
                    else None
                ),
            },
        }
    )


@require_GET
def experiment_frames_api(request, experiment_id: int):
    experiment = get_object_or_404(Experiment, pk=experiment_id)
    try:
        limit = int(request.GET.get("limit", "200"))
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 2000))

    frames = list(
        Frame.objects.filter(experiment=experiment)
        .order_by("-second", "-id")[:limit]
        .values("second", "temperature", "dif_pressure")
    )
    frames.reverse()

    return JsonResponse({"status": "ok", "frames": frames})


_serial_lock = threading.Lock()


@require_POST
def experiment_command_api(request, experiment_id: int):
    """
    POST JSON: {"command": "start"|"stop"}

    IMPORTANT: We update DB state only after Arduino confirmation (ACK).
    """
    experiment = get_object_or_404(Experiment, pk=experiment_id)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"status": "error", "error": "Invalid JSON body."}, status=400)

    cmd = (payload.get("command") or "").strip().lower()
    if cmd not in {"start", "stop"}:
        return JsonResponse({"status": "error", "error": "Unknown command."}, status=400)

    if not experiment.serial_port:
        return JsonResponse(
            {"status": "error", "error": "Experiment serial_port is not configured."},
            status=400,
        )

    wire_cmd = "START" if cmd == "start" else "STOP"

    with _serial_lock:
        sess = get_session(port=experiment.serial_port, baud_rate=experiment.baud_rate)
        res = sess.request_one_line(command=wire_cmd, timeout_s=2.5)

    if not (res.ok and res.confirmed):
        return JsonResponse(
            {
                "status": "error",
                "confirmed": False,
                "error": res.error or "Command failed.",
                "response_lines": res.response_lines,
            },
            status=502,
        )

    now = timezone.now()
    if cmd == "start":
        if experiment.started_at is None:
            experiment.started_at = now
        if experiment.ignited_at is None:
            # For now treat "START" as the trigger moment; adjust when protocol is finalized.
            experiment.ignited_at = now
        experiment.status = Experiment.Status.RUNNING
        experiment.save()
        ensure_poller_running(experiment)
    else:
        if experiment.ended_at is None:
            experiment.ended_at = now
        experiment.status = Experiment.Status.ABORTED
        experiment.save()
        stop_poller(experiment.id)

    return JsonResponse(
        {
            "status": "ok",
            "confirmed": True,
            "command": cmd,
            "experiment": {"id": experiment.id, "status": experiment.status},
            "response_lines": res.response_lines,
        },
        status=200,
    )


@require_POST
def experiment_test_connection_api(request, experiment_id: int):
    experiment = get_object_or_404(Experiment, pk=experiment_id)
    if not experiment.serial_port:
        return JsonResponse(
            {"status": "error", "error": "Experiment serial_port is not configured."},
            status=400,
        )

    with _serial_lock:
        sess = get_session(port=experiment.serial_port, baud_rate=experiment.baud_rate)
        res = sess.request_one_line(command="PING", timeout_s=1.5)

    if not (res.ok and res.confirmed):
        return JsonResponse(
            {
                "status": "error",
                "confirmed": False,
                "error": res.error or "No ACK.",
                "response_lines": res.response_lines,
            },
            status=502,
        )

    return JsonResponse(
        {"status": "ok", "confirmed": True, "response_lines": res.response_lines},
        status=200,
    )


@csrf_exempt
@require_POST
def frame_batch_ingest(request, experiment_id: int):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse(
            {"status": "error", "error": "Invalid JSON body."},
            status=400,
        )

    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except Experiment.DoesNotExist:
        return JsonResponse(
            {"status": "error", "error": "Experiment not found."},
            status=404,
        )

    try:
        created = Frame.bulk_create_from_payload(payload, experiment=experiment)
    except ValueError as exc:
        return JsonResponse(
            {"status": "error", "error": str(exc)},
            status=400,
        )

    return JsonResponse(
        {"status": "ok", "created": len(created)},
        status=201,
    )
