# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
import os
from typing import Any

import vertexai
from dotenv import load_dotenv
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.cloud import logging as google_cloud_logging
from vertexai.agent_engines.templates.adk import AdkApp

from app.agent import app as adk_app
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback

# Load environment variables from .env file at runtime
load_dotenv()

# Initialize Vertex AI at module level with fallbacks to prevent GoogleAuthError on local imports
vertexai.init(
    project=os.environ.get("GOOGLE_CLOUD_PROJECT") or "dummy-project",
    location=os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"
)


class AgentEngineApp(AdkApp):
    def project_id(self) -> str:
        """Returns the project ID, bypassing google auth discovery when running locally."""
        return os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID") or "dummy-project"

    def set_up(self) -> None:
        """Initialize the agent engine app with logging and telemetry."""
        setup_telemetry()
        super().set_up()
        logging.basicConfig(level=logging.INFO)
        is_test = os.environ.get("INTEGRATION_TEST") == "TRUE"
        gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID") or "dummy-project"
        if not is_test and gcp_project != "dummy-project":
            try:
                logging_client = google_cloud_logging.Client()
                self.logger = logging_client.logger(__name__)
                self.use_gcp_logger = True
            except Exception:
                self.logger = logging.getLogger(__name__)
                self.use_gcp_logger = False
        else:
            self.logger = logging.getLogger(__name__)
            self.use_gcp_logger = False
        if gemini_location:
            os.environ["GOOGLE_CLOUD_LOCATION"] = gemini_location

    def register_feedback(self, feedback: dict[str, Any]) -> None:
        """Collect and log feedback."""
        feedback_obj = Feedback.model_validate(feedback)
        if getattr(self, "use_gcp_logger", False):
            self.logger.log_struct(feedback_obj.model_dump(), severity="INFO")
        else:
            self.logger.info(f"Feedback received: {feedback_obj.model_dump()}")

    def register_operations(self) -> dict[str, list[str]]:
        """Registers the operations of the Agent."""
        operations = super().register_operations()
        operations[""] = [*operations.get("", []), "register_feedback"]
        return operations

    def clone(self) -> "AgentEngineApp":
        """Returns a clone of the Agent Runtime application."""
        return self


gemini_location = os.environ.get("GOOGLE_CLOUD_LOCATION")
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")
agent_runtime = AgentEngineApp(
    app=adk_app,
    artifact_service_builder=lambda: (
        GcsArtifactService(bucket_name=logs_bucket_name)
        if logs_bucket_name
        else InMemoryArtifactService()
    ),
)
