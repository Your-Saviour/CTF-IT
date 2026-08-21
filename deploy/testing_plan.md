# Deployment testing

Validate Compose rendering, API health, PostgreSQL persistence, Semaphore guest configuration, Caldera integration, AWS identity/readiness, and DNS separately. Use only disposable, ownership-tagged AWS resources in the approved acceptance account. Confirm the cleanup inventory for the unique acceptance run ID is empty before ending the test.
