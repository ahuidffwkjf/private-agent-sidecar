from agent_sidecar.desktop import main
import sys

if __name__ == '__main__':
    if sys.argv[1:] == ['--tailcat-worker']:
        from agent_sidecar.tailcat_worker import main as worker
        raise SystemExit(worker())
    else:
        main()
