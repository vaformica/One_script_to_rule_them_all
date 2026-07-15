import shlex


def read_status(backend, job_id):
    if not job_id:
        return "Not submitted"
    command = (
        f"state=$(squeue -h -j {shlex.quote(job_id)} -o '%T' | head -1); "
        f"if [[ -n \"$state\" ]]; then echo \"$state\"; "
        f"else sacct -n -P -X -j {shlex.quote(job_id)} "
        "--format=State | head -1 | cut -d'|' -f1; fi"
    )
    return backend.run(command).stdout.strip() or "Unknown"
