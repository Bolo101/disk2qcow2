import subprocess
import sys
import re
import time
import os
import shutil
from log_handler import log_error, log_info, log_warning
from pathlib import Path

def run_command(command_list: list[str], raise_on_error: bool = True) -> str:
    try:
        result = subprocess.run(command_list, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode('utf-8').strip()
    except FileNotFoundError:
        log_error(f"Error: Command not found: {' '.join(command_list)}")
        if raise_on_error:
            sys.exit(2)
        else:
            raise
    except subprocess.CalledProcessError:
        log_error(f"Error: Command execution failed: {' '.join(command_list)}")
        if raise_on_error:
            sys.exit(1)
        else:
            raise
    except KeyboardInterrupt:
        log_error("Operation interrupted by user (Ctrl+C)")
        print("\nOperation interrupted by user (Ctrl+C)")
        sys.exit(130)  # Standard exit code for SIGINT

def run_command_with_progress(command_list: list[str], progress_callback=None, stop_flag=None) -> str:
    """Run command with progress monitoring and cancellation support"""
    try:
        # Start process
        process = subprocess.Popen(command_list, stdout=subprocess.PIPE, 
                                 stderr=subprocess.PIPE, text=True)
        
        # Monitor progress
        while process.poll() is None:
            if stop_flag and stop_flag():
                # User requested cancellation
                process.terminate()
                process.wait()
                raise KeyboardInterrupt("Operation cancelled by user")
            
            # Update progress if callback provided
            if progress_callback:
                progress_callback()
            
            time.sleep(1)
        
        # Wait for completion and get output
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, command_list, stdout, stderr)
        
        return stdout.strip()
        
    except FileNotFoundError:
        log_error(f"Error: Command not found: {' '.join(command_list)}")
        raise
    except subprocess.CalledProcessError as e:
        log_error(f"Error: Command execution failed: {' '.join(command_list)}")
        if e.stderr:
            log_error(f"Error output: {e.stderr}")
        raise
    except KeyboardInterrupt:
        log_error("Operation interrupted by user")
        raise

def get_disk_list() -> list[dict]:
    """
    Get list of available disks as structured data.
    Returns a list of dictionaries with disk information.
    Each dictionary contains: 'device', 'size', 'model', 'size_bytes'.
    """
    try:
        # Use more explicit column specification with -o option and -n to skip header
        output = run_command(["lsblk", "-d", "-o", "NAME,SIZE,TYPE,MODEL", "-n", "-b"])
        
        if not output:
            # Fallback to a simpler command if the first one returned no results
            output = run_command(["lsblk", "-d", "-o", "NAME,SIZE", "-n", "-b"])
            if not output:
                log_info("No disks detected. Ensure the program is run with appropriate permissions.")
                return []
        
        # Parse the output from lsblk command
        disks = []
        for line in output.strip().split('\n'):
            if not line.strip():
                continue
                
            # Split the line but preserve the model name which might contain spaces
            parts = line.strip().split(maxsplit=3)
            device = parts[0]
            
            # Ensure we have at least NAME and SIZE
            if len(parts) >= 2:
                size_bytes = int(parts[1])
                size_human = format_bytes(size_bytes)
                
                # MODEL may be missing, set to "Unknown" if it is
                model = parts[3] if len(parts) > 3 else "Unknown"
                
                disks.append({
                    "device": f"/dev/{device}",
                    "size": size_human,
                    "size_bytes": size_bytes,
                    "model": model
                })
        return disks
    except FileNotFoundError as e:
        log_error(f"Error: Command not found: {str(e)}")
        return []
    except subprocess.CalledProcessError as e:
        log_error(f"Error executing command: {str(e)}")
        return []
    except (IndexError, ValueError) as e:
        log_error(f"Error parsing disk information: {str(e)}")
        return []
    except KeyboardInterrupt:
        log_error("Disk listing interrupted by user")
        return []

def format_bytes(bytes_count: int) -> str:
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_count < 1024.0:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.1f} PB"

def get_directory_space(path: str) -> dict:
    """
    Get available space information for a directory
    Returns dict with 'total', 'used', 'free' in bytes
    """
    try:
        stat = shutil.disk_usage(path)
        return {
            'total': stat.total,
            'used': stat.total - stat.free,
            'free': stat.free
        }
    except OSError as e:
        log_error(f"Error getting disk space for {path}: {str(e)}")
        return {'total': 0, 'used': 0, 'free': 0}

def check_output_space(output_path: str, source_disk_size: int, compression_ratio: float = 0.5) -> tuple[bool, str]:
    """
    Check if output directory has enough space for the VM
    Args:
        output_path: Path to output directory
        source_disk_size: Size of source disk in bytes
        compression_ratio: Expected compression ratio (0.5 = 50% compression)
    Returns:
        tuple: (has_enough_space, message)
    """
    try:
        # Ensure directory exists
        os.makedirs(output_path, exist_ok=True)
        
        space_info = get_directory_space(output_path)
        estimated_vm_size = int(source_disk_size * compression_ratio)
        
        # Add 10% safety margin
        required_space = int(estimated_vm_size * 1.1)
        
        has_space = space_info['free'] >= required_space
        
        message = (
            f"Available space: {format_bytes(space_info['free'])}\n"
            f"Estimated VM size: {format_bytes(estimated_vm_size)}\n"
            f"Required space (with 10% margin): {format_bytes(required_space)}\n"
            f"Status: {'✅ Sufficient space' if has_space else '❌ Insufficient space'}"
        )
        
        return has_space, message
        
    except Exception as e:
        return False, f"Error checking space: {str(e)}"

def check_qemu_tools() -> tuple[bool, str]:
    """Check if required QEMU tools are available"""
    tools = ['qemu-img', 'dd']
    missing = []
    
    for tool in tools:
        try:
            subprocess.run([tool, '--version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            missing.append(tool)
    
    if missing:
        return False, f"Missing required tools: {', '.join(missing)}"
    return True, "All required tools available"

def create_vm_from_disk(source_disk: str, output_path: str, vm_name: str, progress_callback=None, stop_flag=None) -> str:
    """
    Convert physical disk to qcow2 VM
    Args:
        source_disk: Path to source disk (e.g., /dev/sda)
        output_path: Directory to save VM files
        vm_name: Name for the VM files
        progress_callback: Function to call for progress updates
        stop_flag: Function that returns True if operation should stop
    Returns:
        str: Path to created qcow2 file
    """
    try:
        # Create output directory
        os.makedirs(output_path, exist_ok=True)
        
        # Generate file paths
        raw_image_path = os.path.join(output_path, f"{vm_name}_temp.img")
        qcow2_path = os.path.join(output_path, f"{vm_name}.qcow2")
        
        log_info(f"Starting P2V conversion: {source_disk} -> {qcow2_path}")
        
        # Step 1: Create raw image from physical disk using dd
        log_info("Step 1: Creating raw disk image...")
        if progress_callback:
            progress_callback(10, "Creating raw disk image...")
        
        # Get disk size for dd progress monitoring
        disk_info = get_disk_info(source_disk)
        block_size = "1M"  # 1MB blocks for better performance
        
        dd_cmd = [
            'dd',
            f'if={source_disk}',
            f'of={raw_image_path}',
            f'bs={block_size}',
            'conv=sync,noerror',  # Continue on read errors
            'status=progress'  # Show progress
        ]
        
        # Run dd with monitoring
        process = subprocess.Popen(dd_cmd, stderr=subprocess.PIPE, text=True)
        
        while process.poll() is None:
            if stop_flag and stop_flag():
                process.terminate()
                process.wait()
                # Clean up temporary file
                if os.path.exists(raw_image_path):
                    os.remove(raw_image_path)
                raise KeyboardInterrupt("Operation cancelled by user")
            
            if progress_callback:
                progress_callback(50, "Copying disk data...")
            
            time.sleep(2)
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            # Clean up on failure
            if os.path.exists(raw_image_path):
                os.remove(raw_image_path)
            raise subprocess.CalledProcessError(process.returncode, dd_cmd, stdout, stderr)
        
        log_info("Raw disk image created successfully")
        
        # Step 2: Convert raw image to compressed qcow2
        log_info("Step 2: Converting to compressed qcow2...")
        if progress_callback:
            progress_callback(70, "Converting to qcow2 format...")
        
        qemu_cmd = [
            'qemu-img', 'convert',
            '-f', 'raw',           # Input format
            '-O', 'qcow2',         # Output format
            '-c',                  # Compress
            '-p',                  # Show progress
            raw_image_path,        # Input file
            qcow2_path            # Output file
        ]
        
        # Run qemu-img convert
        process = subprocess.Popen(qemu_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        
        while process.poll() is None:
            if stop_flag and stop_flag():
                process.terminate()
                process.wait()
                # Clean up files
                for temp_file in [raw_image_path, qcow2_path]:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                raise KeyboardInterrupt("Operation cancelled by user")
            
            if progress_callback:
                progress_callback(85, "Compressing virtual machine...")
            
            time.sleep(1)
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            # Clean up on failure
            for temp_file in [raw_image_path, qcow2_path]:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            raise subprocess.CalledProcessError(process.returncode, qemu_cmd, stdout, stderr)
        
        # Step 3: Clean up temporary raw image
        log_info("Step 3: Cleaning up temporary files...")
        if progress_callback:
            progress_callback(95, "Cleaning up...")
        
        os.remove(raw_image_path)
        
        # Step 4: Verify output file
        if not os.path.exists(qcow2_path):
            raise Exception("qcow2 file was not created successfully")
        
        final_size = os.path.getsize(qcow2_path)
        log_info(f"P2V conversion completed successfully")
        log_info(f"Output file: {qcow2_path}")
        log_info(f"Final VM size: {format_bytes(final_size)}")
        
        if progress_callback:
            progress_callback(100, "Conversion completed successfully!")
        
        return qcow2_path
        
    except KeyboardInterrupt:
        log_error("P2V conversion cancelled by user")
        raise
    except Exception as e:
        log_error(f"P2V conversion failed: {str(e)}")
        raise

def get_disk_info(device: str) -> dict:
    """Get detailed information about a disk"""
    try:
        # Get size using blockdev
        result = subprocess.run(['blockdev', '--getsize64', device], 
                              capture_output=True, text=True, check=True)
        size_bytes = int(result.stdout.strip())
        
        # Get model info using lsblk
        result = subprocess.run(['lsblk', '-d', '-n', '-o', 'MODEL', device], 
                              capture_output=True, text=True, check=True)
        model = result.stdout.strip() or "Unknown"
        
        return {
            'device': device,
            'size_bytes': size_bytes,
            'size_human': format_bytes(size_bytes),
            'model': model
        }
        
    except Exception as e:
        log_error(f"Error getting disk info for {device}: {str(e)}")
        return {
            'device': device,
            'size_bytes': 0,
            'size_human': "Unknown",
            'model': "Unknown"
        }

def validate_vm_name(name: str) -> tuple[bool, str]:
    """Validate VM name for filesystem compatibility"""
    if not name:
        return False, "VM name cannot be empty"
    
    # Check for invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        if char in name:
            return False, f"VM name contains invalid character: {char}"
    
    # Check length
    if len(name) > 100:
        return False, "VM name is too long (max 100 characters)"
    
    # Check for reserved names
    reserved = ['con', 'prn', 'aux', 'nul', 'com1', 'com2', 'com3', 'com4', 'com5', 'com6', 'com7', 'com8', 'com9', 'lpt1', 'lpt2', 'lpt3', 'lpt4', 'lpt5', 'lpt6', 'lpt7', 'lpt8', 'lpt9']
    if name.lower() in reserved:
        return False, f"VM name '{name}' is reserved"
    
    return True, "Valid name"

def get_active_disk():
    """
    Detect the active device backing the root filesystem.
    Returns a list of devices or None for consistency with original code.
    """
    try:
        # Initialize devices set for collecting all active devices
        devices = set()
        
        # Check /proc/mounts for root filesystem
        with open('/proc/mounts', 'r') as f:
            for line in f:
                if line.strip() and ' / ' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        root_device = parts[0]
                        
                        # Extract device name with improved regex for NVMe
                        match = re.search(r'/dev/([a-zA-Z]+\d*[a-zA-Z]*\d*)', root_device)
                        if match:
                            device_name = match.group(1)
                            # Get base disk name
                            base_device = get_base_disk(device_name)
                            devices.add(base_device)
                        break
        
        if devices:
            return list(devices)
        else:
            log_error("No active devices found")
            return None

    except Exception as e:
        log_error(f"Error detecting active disk: {str(e)}")
        return None

def get_base_disk(device_name: str) -> str:
    """
    Extract base disk name from a device name.
    Examples: 
        'nvme0n1p1' -> 'nvme0n1'
        'sda1' -> 'sda'
        'nvme0n1' -> 'nvme0n1'
    """
    try:
        # Handle nvme devices (e.g., nvme0n1p1 -> nvme0n1)
        if 'nvme' in device_name:
            match = re.match(r'(nvme\d+n\d+)', device_name)
            if match:
                return match.group(1)
        
        # Handle traditional devices (e.g., sda1 -> sda)
        match = re.match(r'([a-zA-Z/]+[a-zA-Z])', device_name)
        if match:
            return match.group(1)
        
        # If no pattern matches, return the original
        return device_name
        
    except Exception as e:
        log_error(f"Error processing device name '{device_name}': {str(e)}")
        return device_name