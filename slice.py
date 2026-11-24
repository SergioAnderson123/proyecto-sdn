#!/usr/bin/env python3
import argparse
import subprocess
import sys

def run_cmd(cmd):
    print(f"Running: {cmd}")
    subprocess.run(cmd, shell=True, check=True)

def cmd_up(args):
    print(f"Deploying with inventory: {args.inventory} and overlay: {args.overlay}")
    # Logic to iterate over inventory and bring up infra/ components
    # followed by overlays/ configuration.
    run_cmd("echo 'Deploying infra...'")
    if args.overlay:
        run_cmd(f"echo 'Applying overlay {args.overlay}...'")

def cmd_down(args):
    print("Tearing down infrastructure...")

def main():
    parser = argparse.ArgumentParser(description="Campus SDN Deployer")
    subparsers = parser.add_subparsers()
    
    up = subparsers.add_parser('up')
    up.add_argument('--inventory', required=True)
    up.add_argument('--overlay')
    up.set_defaults(func=cmd_up)
    
    down = subparsers.add_parser('down')
    down.add_argument('--inventory', required=True)
    down.set_defaults(func=cmd_down)
    
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
