# Service credentials test plan

The service catalogue may show non-secret AWS provider status (account/region/role readiness), but it must not accept, encrypt, display, or persist AWS access keys. Verify that existing application, Semaphore, Caldera, Cloudflare, and training credentials retain their existing authorization and redaction behavior.
