# Beetle IDtracker Unified Pipeline v0.9.20

This release hardens the Mac GUI for intermittent VPN and internet connections.

## Key changes

- SSH connections fail quickly rather than leaving macOS beach-balling.
- Keepalive checks detect dead VPN connections.
- Safe read-only operations retry with bounded exponential backoff.
- SSH connection multiplexing reuses one connection during scans and submissions.
- TOMLs are fetched in batches of 100, dramatically reducing network round trips.
- The final `sbatch` operation is not automatically repeated after an uncertain disconnect, preventing accidental duplicate submissions.
- Existing Firebird recovery remains the mechanism for reconnecting to runs after network loss.
