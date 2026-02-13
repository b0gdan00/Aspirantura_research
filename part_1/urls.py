from django.urls import path

from . import views

urlpatterns = [
    path("", views.empty_page, name="empty_page"),
    path("experiments/", views.experiments_list, name="experiments_list"),
    path("experiments/new/", views.experiment_create, name="experiment_create"),
    path(
        "experiments/<int:experiment_id>/",
        views.experiment_detail,
        name="experiment_detail",
    ),
    path(
        "experiments/<int:experiment_id>/action/",
        views.experiment_action,
        name="experiment_action",
    ),
    path(
        "api/experiments/<int:experiment_id>/frames/batch/",
        views.frame_batch_ingest,
        name="frame_batch_ingest",
    ),
    path(
        "api/experiments/<int:experiment_id>/command/",
        views.experiment_command_api,
        name="experiment_command_api",
    ),
    path(
        "api/experiments/<int:experiment_id>/test-connection/",
        views.experiment_test_connection_api,
        name="experiment_test_connection_api",
    ),
    path(
        "api/experiments/<int:experiment_id>/summary/",
        views.experiment_summary_api,
        name="experiment_summary_api",
    ),
    path(
        "api/experiments/<int:experiment_id>/frames/",
        views.experiment_frames_api,
        name="experiment_frames_api",
    ),
]
