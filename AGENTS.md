# Recordkeeper
Python-first modular monolith. Keep provider adapters separate from backup logic.
Never commit credentials or listening history. Credentials live in KeePassXC.
Account writes are disabled until separately implemented and approved.
Test every behavior change, including interrupted pagination and repeat listens.
Do not claim backup completeness from a successful HTTP response alone.
Use uv.lock for reproducibility. Add UI incrementally after verified ingestion.
