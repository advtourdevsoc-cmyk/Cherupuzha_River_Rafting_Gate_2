#!/usr/bin/env python3
"""
RAM Usage Analyzer for Ayannur Raft Booking Project
Measures memory consumption of the application during runtime
"""

import os
import sys
import time
import psutil
import subprocess
from datetime import datetime
from pathlib import Path

# Project directory
PROJECT_DIR = Path(__file__).parent.absolute()


def print_header(text):
    """Print formatted header"""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def get_package_sizes():
    """Estimate RAM usage of key dependencies"""
    print_header("1. DEPENDENCY ANALYSIS")
    
    # Key packages and their approximate memory footprints
    packages_info = {
        "Flask": {"base": 2.0, "per_request": 0.5},
        "PyMongo": {"base": 3.5, "per_connection": 1.0},
        "bcrypt": {"base": 0.8, "per_operation": 0.2},
        "pymongo": {"base": 3.5, "description": "MongoDB driver"},
        "dnspython": {"base": 0.5},
        "gunicorn": {"base": 5.0, "per_worker": 15.0},
        "Flask-Login": {"base": 0.5},
        "Flask-PyMongo": {"base": 0.3},
        "razorpay": {"base": 0.4},
        "python-dotenv": {"base": 0.2},
    }
    
    total_base = 0
    print(f"\n{'Package':<20} {'Estimated RAM (MB)':<20} {'Notes':<30}")
    print("-" * 70)
    
    for package, info in packages_info.items():
        base_mb = info.get("base", 0)
        total_base += base_mb
        notes = info.get("description", "")
        print(f"{package:<20} {base_mb:<20.2f} {notes:<30}")
    
    print("-" * 70)
    print(f"{'TOTAL BASE DEPENDENCIES':<20} {total_base:<20.2f}")
    
    return total_base


def analyze_application_size():
    """Analyze the application code size"""
    print_header("2. APPLICATION CODE SIZE ANALYSIS")
    
    total_size = 0
    file_count = 0
    
    extensions_to_check = ['.py', '.html', '.js', '.css']
    
    for ext in extensions_to_check:
        ext_files = list(PROJECT_DIR.rglob(f"*{ext}"))
        ext_size = sum(f.stat().st_size for f in ext_files if f.is_file())
        ext_size_mb = ext_size / (1024 * 1024)
        total_size += ext_size
        file_count += len(ext_files)
        print(f"{ext:<10} files: {len(ext_files):<5}  size: {ext_size_mb:>8.2f} MB")
    
    total_size_mb = total_size / (1024 * 1024)
    print(f"\n{'Total code + templates':<30} {total_size_mb:.2f} MB")
    print(f"{'Estimated loaded in RAM':<30} {total_size_mb * 0.3:.2f} MB (30% in memory)")
    
    return total_size_mb * 0.3


def measure_runtime_ram():
    """Measure actual RAM usage when application starts"""
    print_header("3. RUNTIME RAM MEASUREMENT")
    
    # Check if MongoDB is running
    try:
        import pymongo
        client = pymongo.MongoClient('mongodb://127.0.0.1:27017/', serverSelectionTimeoutMS=2000)
        client.admin.command('ping')
        print("✓ MongoDB is running and accessible")
        mongo_available = True
    except Exception as e:
        print(f"✗ MongoDB not available: {e}")
        print("  Note: RAM usage will be measured without database connection")
        mongo_available = False
    
    # Try to import and measure app
    try:
        print("\nStarting Flask application measurement...")
        print("Setting up application context...")
        
        # Start a subprocess to measure RAM usage
        script = """
import sys
import os
import time
import psutil
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.absolute()
sys.path.insert(0, str(PROJECT_DIR))

# Get PID for monitoring
pid = os.getpid()
process = psutil.Process(pid)

# Import and create app
try:
    from app import create_app
    app = create_app()
    
    # Get initial RAM
    initial_ram = process.memory_info().rss / (1024 * 1024)  # MB
    print(f"INITIAL_RAM:{initial_ram:.2f}")
    
    # Create app context and measure
    with app.app_context():
        with_context_ram = process.memory_info().rss / (1024 * 1024)
        print(f"WITH_CONTEXT_RAM:{with_context_ram:.2f}")
    
    # Final measurement
    final_ram = process.memory_info().rss / (1024 * 1024)
    print(f"FINAL_RAM:{final_ram:.2f}")
    
except Exception as e:
    print(f"ERROR:{str(e)}")
"""
        
        result = subprocess.run(
            [sys.executable, '-c', script],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Parse results
        measurements = {}
        for line in result.stdout.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                try:
                    measurements[key] = float(value)
                except:
                    pass
        
        if measurements:
            print(f"\nMemory Measurements:")
            print(f"  Initial RAM (before imports):       {measurements.get('INITIAL_RAM', 0):.2f} MB")
            print(f"  RAM with app context:              {measurements.get('WITH_CONTEXT_RAM', 0):.2f} MB")
            print(f"  Final RAM:                         {measurements.get('FINAL_RAM', 0):.2f} MB")
            print(f"  RAM increase from imports:         {measurements.get('WITH_CONTEXT_RAM', 0) - measurements.get('INITIAL_RAM', 0):.2f} MB")
            
            return measurements.get('FINAL_RAM', 0)
        else:
            if result.stderr:
                print(f"Error details: {result.stderr}")
            return None
            
    except Exception as e:
        print(f"Could not measure runtime RAM: {e}")
        return None


def calculate_with_gunicorn():
    """Calculate RAM for Gunicorn deployment"""
    print_header("4. GUNICORN DEPLOYMENT ESTIMATE")
    
    print("\nGunicorn Configuration Impact:")
    print("  Base process:                  ~15-20 MB")
    print("  Per worker (with Flask app):   ~30-50 MB")
    print("  MongoDB connection pool:       ~5-10 MB per connection")
    
    workers = 4  # typical configuration
    per_worker = 40  # MB
    
    print(f"\nAssuming typical setup: {workers} workers")
    total_gunicorn = workers * per_worker + 20
    print(f"  Total estimated: {total_gunicorn} MB")
    
    return total_gunicorn


def generate_report(dep_ram, code_ram, runtime_ram, gunicorn_ram):
    """Generate comprehensive RAM usage report"""
    print_header("COMPREHENSIVE RAM USAGE REPORT")
    
    print("\n📊 BREAKDOWN BY COMPONENT:\n")
    
    estimates = {
        "Base Python + Dependencies": dep_ram,
        "Application Code (loaded)": code_ram,
        "MongoDB Connection Pool": 8.0,
        "Session Storage": 2.0,
        "Caching Layer (if any)": 1.0,
    }
    
    total_minimal = sum(estimates.values())
    
    for component, ram_mb in estimates.items():
        percentage = (ram_mb / total_minimal * 100) if total_minimal > 0 else 0
        print(f"  {component:<35} {ram_mb:>8.2f} MB  ({percentage:>5.1f}%)")
    
    print("\n" + "-" * 70)
    print(f"  {'Minimal Setup (Single Process)':<35} {total_minimal:>8.2f} MB")
    
    # Production estimate
    prod_total = total_minimal * 4  # 4 workers
    print(f"  {'Production (4 Gunicorn workers)':<35} {prod_total:>8.2f} MB")
    
    # Under load estimate
    under_load = total_minimal * 1.3  # 30% increase under load
    print(f"  {'Under Load (Single Process +30%)':<35} {under_load:>8.2f} MB")
    
    print("\n" + "=" * 70)
    print("\n📌 RECOMMENDATIONS:\n")
    print("  • Development:    Minimum 512 MB available RAM")
    print("  • Production:     Minimum 2-4 GB for 4 workers")
    print("  • High Traffic:   Recommend 4-8 GB (with load balancing)")
    
    if runtime_ram:
        print(f"\n✓ Actual measured runtime RAM: {runtime_ram:.2f} MB")


def main():
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + "  AYANNUR RAFT BOOKING - RAM USAGE ANALYZER".center(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "=" * 68 + "╝")
    
    print(f"\nAnalysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Project Path: {PROJECT_DIR}")
    
    # Run analysis
    dep_ram = get_package_sizes()
    code_ram = analyze_application_size()
    runtime_ram = measure_runtime_ram()
    gunicorn_ram = calculate_with_gunicorn()
    
    # Generate final report
    generate_report(dep_ram, code_ram, runtime_ram, gunicorn_ram)
    
    print("\n" + "=" * 70)
    print("Analysis complete!\n")


if __name__ == "__main__":
    main()
