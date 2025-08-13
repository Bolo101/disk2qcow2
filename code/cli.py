#!/usr/bin/env python3
"""
Physical to Virtual (P2V) Converter - Command Line Interface Module
Converts physical disks to qcow2 virtual machine format
"""

import argparse
import sys
import os
import signal
import time
from pathlib import Path
from typing import Optional, Dict, Any

# Import modules from the existing codebase
from log_handler import log_info, log_error, log_warning, generate_session_pdf, generate_log_file_pdf
from utils import (
    get_disk_list, 
    get_directory_space, 
    check_output_space, 
    check_qemu_tools,
    create_vm_from_disk, 
    validate_vm_name, 
    format_bytes, 
    get_active_disk,
    get_disk_info
)

class P2VConverterCLI:
    """Command Line Interface for P2V Converter"""
    
    def __init__(self):
        self.session_logs = []
        self.stop_requested = False
        self.conversion_started = False
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle interrupt signals gracefully"""
        print(f"\n🛑 Received signal {signum}. Stopping operation...")
        self.stop_requested = True
        if self.conversion_started:
            print("⏳ Waiting for current operation to complete safely...")
            time.sleep(2)
        sys.exit(130)
    
    def add_session_log(self, message: str, level: str = "INFO"):
        """Add message to session logs"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"{timestamp} - {level} - {message}"
        self.session_logs.append(formatted_message)
    
    def print_header(self):
        """Print application header"""
        print("=" * 70)
        print("🖥️ Physical to Virtual (P2V) Converter - CLI")
        print("=" * 70)
        print()
    
    def check_prerequisites(self) -> bool:
        """Check if required tools are available"""
        print("🔍 Checking prerequisites...")
        tools_available, message = check_qemu_tools()
        
        if not tools_available:
            print(f"❌ Prerequisites check failed: {message}")
            print("\nRequired packages:")
            print("  • qemu-utils (for qemu-img)")
            print("  • coreutils (for dd)")
            self.add_session_log(f"Prerequisites check failed: {message}", "ERROR")
            return False
        
        print("✅ All prerequisites are available")
        self.add_session_log("All prerequisites are available", "SUCCESS")
        return True
    
    def list_disks(self, show_details: bool = False) -> Dict[str, Dict]:
        """List available disks"""
        print("💿 Scanning for available disks...")
        disks = get_disk_list()
        disk_dict = {}
        
        if not disks:
            print("❌ No disks found")
            return disk_dict
        
        print(f"\n📋 Found {len(disks)} disk(s):")
        print("-" * 70)
        
        # Get active disks to mark system disk
        active_disks = get_active_disk() or []
        
        for i, disk in enumerate(disks, 1):
            # Check if this is a system disk
            is_system = any(f"/dev/{active}" in disk['device'] for active in active_disks)
            system_marker = " 🟡 [SYSTEM DISK]" if is_system else ""
            
            print(f"{i:2d}. {disk['device']} - {disk['size']} - {disk['model']}{system_marker}")
            
            if show_details:
                print(f"    Size in bytes: {disk['size_bytes']:,}")
            
            disk_dict[str(i)] = disk
            disk_dict[disk['device']] = disk
        
        print("-" * 70)
        return disk_dict
    
    def select_disk_interactive(self) -> Optional[str]:
        """Interactively select a disk"""
        disk_dict = self.list_disks()
        
        if not disk_dict:
            return None
        
        print("\n🎯 Select source disk:")
        while True:
            try:
                choice = input("Enter disk number or device path (or 'q' to quit): ").strip()
                
                if choice.lower() == 'q':
                    return None
                
                if choice in disk_dict:
                    selected_disk = disk_dict[choice]['device']
                    print(f"✅ Selected: {selected_disk}")
                    return selected_disk
                else:
                    print("❌ Invalid selection. Please try again.")
            
            except KeyboardInterrupt:
                print("\n👋 Operation cancelled")
                return None
            except EOFError:
                print("\n👋 End of input - operation cancelled")
                return None
    
    def get_vm_name_interactive(self, default_name: str) -> Optional[str]:
        """Interactively get VM name"""
        print(f"\n📝 VM Configuration:")
        print(f"Default VM name: {default_name}")
        
        while True:
            try:
                vm_name = input(f"Enter VM name (press Enter for '{default_name}'): ").strip()
                
                if not vm_name:
                    vm_name = default_name
                
                is_valid, message = validate_vm_name(vm_name)
                if is_valid:
                    print(f"✅ VM name: {vm_name}")
                    return vm_name
                else:
                    print(f"❌ Invalid name: {message}")
            
            except KeyboardInterrupt:
                print("\n👋 Operation cancelled")
                return None
            except EOFError:
                print("\n👋 End of input - operation cancelled")
                return None
    
    def get_output_dir_interactive(self, default_dir: str) -> Optional[str]:
        """Interactively get output directory"""
        print(f"Default output directory: {default_dir}")
        
        while True:
            try:
                output_dir = input(f"Enter output directory (press Enter for '{default_dir}'): ").strip()
                
                if not output_dir:
                    output_dir = default_dir
                
                # Create directory if it doesn't exist
                try:
                    os.makedirs(output_dir, exist_ok=True)
                    print(f"✅ Output directory: {output_dir}")
                    return output_dir
                except PermissionError:
                    print(f"❌ Permission denied: Cannot create directory '{output_dir}'")
                except OSError as e:
                    if e.errno == 28:  # No space left on device
                        print(f"❌ No space left on device: Cannot create directory '{output_dir}'")
                    elif e.errno == 30:  # Read-only file system
                        print(f"❌ Read-only file system: Cannot create directory '{output_dir}'")
                    else:
                        print(f"❌ OS error creating directory: {e}")
                except FileExistsError:
                    # This shouldn't happen with exist_ok=True, but just in case
                    print(f"❌ Directory already exists and cannot be accessed: {output_dir}")
            
            except KeyboardInterrupt:
                print("\n👋 Operation cancelled")
                return None
            except EOFError:
                print("\n👋 End of input - operation cancelled")
                return None
    
    def check_space_requirements(self, source_device: str, output_dir: str) -> bool:
        """Check space requirements"""
        print(f"\n💾 Checking space requirements...")
        
        try:
            # Get disk info
            disk_info = get_disk_info(source_device)
            disk_size = disk_info['size_bytes']
            
            print(f"Source disk: {source_device}")
            print(f"Source size: {format_bytes(disk_size)}")
            print(f"Output directory: {output_dir}")
            
            # Check available space
            has_space, space_message = check_output_space(output_dir, disk_size)
            
            print(f"\n{space_message}")
            
            if has_space:
                self.add_session_log("Space check passed", "SUCCESS")
                return True
            else:
                self.add_session_log("Space check failed - insufficient space", "ERROR")
                return False
                
        except FileNotFoundError:
            error_msg = f"Source device not found: {source_device}"
            print(f"❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            return False
        except PermissionError:
            error_msg = f"Permission denied accessing source device: {source_device}"
            print(f"❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            return False
        except OSError as e:
            if e.errno == 2:  # No such file or directory
                error_msg = f"Source device not found: {source_device}"
            elif e.errno == 13:  # Permission denied
                error_msg = f"Permission denied accessing source device: {source_device}"
            elif e.errno == 5:  # Input/output error
                error_msg = f"I/O error accessing source device: {source_device}"
            else:
                error_msg = f"OS error accessing source device {source_device}: {e}"
            print(f"❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            return False
        except ValueError as e:
            error_msg = f"Invalid disk size or format for {source_device}: {e}"
            print(f"❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            return False
        except KeyError as e:
            error_msg = f"Missing disk information for {source_device}: {e}"
            print(f"❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            return False
    
    def confirm_conversion(self, source_device: str, vm_name: str, output_dir: str) -> bool:
        """Confirm conversion parameters"""
        print(f"\n📋 Conversion Summary:")
        print("-" * 50)
        print(f"Source disk:      {source_device}")
        print(f"VM name:          {vm_name}")
        print(f"Output directory: {output_dir}")
        print(f"Output file:      {output_dir}/{vm_name}.qcow2")
        print("-" * 50)
        
        # Check if system disk
        active_disks = get_active_disk() or []
        is_system = any(f"/dev/{active}" in source_device for active in active_disks)
        
        if is_system:
            print("⚠️ WARNING: You are about to convert the SYSTEM DISK!")
            print("   This operation will read from the active system disk.")
            print("   System performance may be affected during conversion.")
            print()
        
        print("⚠️ This operation may take a significant amount of time.")
        print("   The process will create a compressed qcow2 virtual machine.")
        print()
        
        try:
            confirm = input("Do you want to proceed? (yes/no): ").strip().lower()
            return confirm in ('yes', 'y')
        except KeyboardInterrupt:
            print("\n👋 Operation cancelled")
            return False
        except EOFError:
            print("\n👋 End of input - operation cancelled")
            return False
    
    def progress_callback(self, percent: float, status: str):
        """Progress callback for conversion"""
        # Create a simple progress bar
        bar_length = 40
        filled_length = int(bar_length * percent // 100)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        # Clear line and print progress
        print(f"\r🔄 [{bar}] {percent:5.1f}% - {status}", end='', flush=True)
        
        self.add_session_log(f"Progress: {percent:.1f}% - {status}")
    
    def stop_check(self) -> bool:
        """Check if stop was requested"""
        return self.stop_requested
    
    def convert_disk(self, source_device: str, output_dir: str, vm_name: str) -> bool:
        """Perform the disk conversion"""
        try:
            print(f"\n🚀 Starting P2V conversion...")
            print(f"Press Ctrl+C to cancel the operation safely.\n")
            
            self.conversion_started = True
            self.add_session_log(f"P2V conversion started: {source_device} -> {vm_name}")
            log_info(f"P2V conversion started: {source_device} -> {vm_name}")
            
            # Perform conversion
            output_file = create_vm_from_disk(
                source_device, 
                output_dir, 
                vm_name,
                self.progress_callback,
                self.stop_check
            )
            
            if not self.stop_requested:
                print(f"\n✅ P2V conversion completed successfully!")
                
                # Get final file info
                final_size = os.path.getsize(output_file)
                print(f"📁 VM file: {output_file}")
                print(f"📏 Size: {format_bytes(final_size)}")
                
                print(f"\n💡 You can now use this qcow2 file with:")
                print(f"   • QEMU/KVM")
                print(f"   • VirtualBox (after conversion)")
                print(f"   • Other virtualization platforms supporting qcow2")
                
                self.add_session_log("P2V conversion completed successfully", "SUCCESS")
                log_info("P2V conversion completed successfully")
                return True
            else:
                print(f"\n⚠️ Conversion was cancelled by user")
                self.add_session_log("P2V conversion cancelled by user", "WARNING")
                return False
                
        except KeyboardInterrupt:
            print(f"\n⚠️ Conversion cancelled by user")
            self.add_session_log("P2V conversion cancelled by user", "WARNING")
            return False
        except FileNotFoundError as e:
            error_msg = f"File not found during conversion: {e}"
            print(f"\n❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            return False
        except PermissionError as e:
            error_msg = f"Permission denied during conversion: {e}"
            print(f"\n❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            return False
        except OSError as e:
            if e.errno == 28:  # No space left on device
                error_msg = "No space left on device during conversion"
            elif e.errno == 5:  # Input/output error
                error_msg = f"I/O error during conversion: {e}"
            elif e.errno == 16:  # Device or resource busy
                error_msg = f"Device busy during conversion: {e}"
            else:
                error_msg = f"OS error during conversion: {e}"
            print(f"\n❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            return False
        except ValueError as e:
            error_msg = f"Invalid parameter during conversion: {e}"
            print(f"\n❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            return False
        except RuntimeError as e:
            error_msg = f"Runtime error during conversion: {e}"
            print(f"\n❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            return False
        except MemoryError:
            error_msg = "Insufficient memory for conversion operation"
            print(f"\n❌ {error_msg}")
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            return False
        finally:
            self.conversion_started = False
    
    def generate_pdf_report(self, report_type: str = "session") -> bool:
        """Generate PDF report"""
        try:
            print(f"\n📄 Generating {report_type} PDF report...")
            
            if report_type == "session":
                pdf_path = generate_session_pdf(self.session_logs)
                print(f"✅ Session PDF report generated: {pdf_path}")
            else:
                pdf_path = generate_log_file_pdf()
                print(f"✅ Complete log PDF report generated: {pdf_path}")
            
            return True
            
        except FileNotFoundError as e:
            print(f"❌ File not found for PDF generation: {e}")
            return False
        except PermissionError as e:
            print(f"❌ Permission denied for PDF generation: {e}")
            return False
        except ImportError as e:
            print(f"❌ Missing required library for PDF generation: {e}")
            return False
        except OSError as e:
            if e.errno == 28:  # No space left on device
                print(f"❌ No space left on device for PDF generation")
            else:
                print(f"❌ OS error during PDF generation: {e}")
            return False
        except ValueError as e:
            print(f"❌ Invalid data for PDF generation: {e}")
            return False
    
    def run_interactive(self):
        """Run interactive CLI mode"""
        self.print_header()
        
        # Check prerequisites
        if not self.check_prerequisites():
            return 1
        
        print("🎮 Interactive Mode - Follow the prompts to convert your disk\n")
        
        # Step 1: Select source disk
        source_device = self.select_disk_interactive()
        if not source_device:
            print("👋 Goodbye!")
            return 0
        
        # Step 2: Get VM name
        default_vm_name = f"{source_device.split('/')[-1]}_vm"
        vm_name = self.get_vm_name_interactive(default_vm_name)
        if not vm_name:
            return 0
        
        # Step 3: Get output directory
        default_output = "/tmp/p2v_output"
        output_dir = self.get_output_dir_interactive(default_output)
        if not output_dir:
            return 0
        
        # Step 4: Check space requirements
        if not self.check_space_requirements(source_device, output_dir):
            print("\n❌ Insufficient space. Operation cancelled.")
            return 1
        
        # Step 5: Confirm conversion
        if not self.confirm_conversion(source_device, vm_name, output_dir):
            print("👋 Operation cancelled")
            return 0
        
        # Step 6: Perform conversion
        success = self.convert_disk(source_device, output_dir, vm_name)
        
        # Step 7: Offer PDF report
        if success:
            try:
                generate_pdf = input(f"\nGenerate session PDF report? (y/n): ").strip().lower()
                if generate_pdf in ('y', 'yes'):
                    self.generate_pdf_report("session")
            except KeyboardInterrupt:
                pass
            except EOFError:
                pass
        
        return 0 if success else 1
    
    def run_batch(self, args):
        """Run in batch mode with provided arguments"""
        self.print_header()
        
        # Check prerequisites
        if not self.check_prerequisites():
            return 1
        
        print("🤖 Batch Mode - Processing provided arguments\n")
        
        # Validate source device
        source_device = args.source
        if not os.path.exists(source_device):
            print(f"❌ Source device not found: {source_device}")
            return 1
        
        # Validate VM name
        vm_name = args.name
        is_valid, message = validate_vm_name(vm_name)
        if not is_valid:
            print(f"❌ Invalid VM name: {message}")
            return 1
        
        # Prepare output directory
        output_dir = args.output
        try:
            os.makedirs(output_dir, exist_ok=True)
        except PermissionError:
            print(f"❌ Permission denied: Cannot create output directory '{output_dir}'")
            return 1
        except OSError as e:
            if e.errno == 28:  # No space left on device
                print(f"❌ No space left on device: Cannot create output directory '{output_dir}'")
            elif e.errno == 30:  # Read-only file system
                print(f"❌ Read-only file system: Cannot create directory '{output_dir}'")
            else:
                print(f"❌ OS error creating output directory: {e}")
            return 1
        except FileExistsError:
            print(f"❌ Directory already exists and cannot be accessed: {output_dir}")
            return 1
        
        # Check space if requested
        if not args.skip_space_check:
            if not self.check_space_requirements(source_device, output_dir):
                if not args.force:
                    print("❌ Insufficient space. Use --force to override or --skip-space-check to skip.")
                    return 1
                else:
                    print("⚠️ Insufficient space detected, but --force specified. Continuing...")
        
        # Show conversion info
        print(f"📋 Conversion Parameters:")
        print(f"   Source: {source_device}")
        print(f"   VM Name: {vm_name}")
        print(f"   Output: {output_dir}")
        
        # Skip confirmation if --yes specified
        if not args.yes:
            if not self.confirm_conversion(source_device, vm_name, output_dir):
                print("👋 Operation cancelled")
                return 0
        
        # Perform conversion
        success = self.convert_disk(source_device, output_dir, vm_name)
        
        # Generate PDF if requested
        if success and args.generate_pdf:
            self.generate_pdf_report("session")
        
        return 0 if success else 1


def run_cli_main():
    """Main entry point for CLI mode"""
    parser = argparse.ArgumentParser(
        description="Physical to Virtual (P2V) Converter - Convert physical disks to qcow2 format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  sudo python3 main.py --cli

  # List available disks
  sudo python3 main.py --cli --list-disks

  # Batch conversion
  sudo python3 main.py --cli -s /dev/sda -n my_vm -o /path/to/output

  # Batch with all options
  sudo python3 main.py --cli -s /dev/sdb -n backup_vm -o /backup --yes --force --generate-pdf

Note: This program must be run with root privileges (sudo).
        """
    )
    
    # CLI mode flag (handled by main.py)
    parser.add_argument("--cli", action="store_true", help=argparse.SUPPRESS)
    
    # Operation modes
    parser.add_argument("--list-disks", "-l", action="store_true",
                       help="List available disks and exit")
    parser.add_argument("--list-disks-detailed", action="store_true",
                       help="List available disks with detailed information")
    
    # Batch mode arguments
    parser.add_argument("--source", "-s", metavar="DEVICE",
                       help="Source disk device (e.g., /dev/sda)")
    parser.add_argument("--name", "-n", metavar="NAME",
                       help="VM name for output files")
    parser.add_argument("--output", "-o", metavar="DIR", default="/tmp/p2v_output",
                       help="Output directory (default: /tmp/p2v_output)")
    
    # Batch mode options
    parser.add_argument("--yes", "-y", action="store_true",
                       help="Skip confirmation prompts")
    parser.add_argument("--force", "-f", action="store_true",
                       help="Force conversion even with insufficient space")
    parser.add_argument("--skip-space-check", action="store_true",
                       help="Skip space requirement checking")
    parser.add_argument("--generate-pdf", action="store_true",
                       help="Generate PDF report after conversion")
    
    # Utility options
    parser.add_argument("--check-tools", action="store_true",
                       help="Check if required tools are available")
    parser.add_argument("--generate-log-pdf", action="store_true",
                       help="Generate PDF from complete log file")
    
    args = parser.parse_args()
    
    # Initialize CLI
    cli = P2VConverterCLI()
    
    # Handle utility commands
    if args.check_tools:
        cli.print_header()
        return 0 if cli.check_prerequisites() else 1
    
    if args.generate_log_pdf:
        cli.print_header()
        return 0 if cli.generate_pdf_report("complete") else 1
    
    if args.list_disks or args.list_disks_detailed:
        cli.print_header()
        cli.list_disks(show_details=args.list_disks_detailed)
        return 0
    
    # Determine mode based on arguments
    batch_mode = bool(args.source and args.name)
    
    if batch_mode:
        return cli.run_batch(args)
    else:
        return cli.run_interactive()


if __name__ == "__main__":
    # This allows the CLI module to be run directly for testing
    try:
        # Check if running as root
        if os.geteuid() != 0:
            print("❌ This program must be run as root (use sudo)")
            print("   Example: sudo python3 main.py --cli")
            sys.exit(1)
        
        sys.exit(run_cli_main())
    except KeyboardInterrupt:
        print("\n👋 Operation cancelled by user")
        sys.exit(130)
    except SystemExit:
        # Re-raise SystemExit to maintain proper exit codes
        raise
    except ImportError as e:
        print(f"\n❌ Missing required module: {e}")
        sys.exit(1)
    except PermissionError:
        print(f"\n❌ Permission denied - ensure you're running with sufficient privileges")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\n❌ Required file not found: {e}")
        sys.exit(1)
    except OSError as e:
        print(f"\n❌ Operating system error: {e}")
        sys.exit(1)