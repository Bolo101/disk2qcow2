#!/usr/bin/env python3

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import subprocess
import threading
import time
from log_handler import log_info, log_error, log_warning, generate_session_pdf, generate_log_file_pdf
from utils import (get_disk_list, get_directory_space, check_output_space, check_qemu_tools, 
                   create_vm_from_disk, validate_vm_name, format_bytes, get_active_disk)

class P2VConverterGUI:
    """GUI class for the P2V Converter application"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Physical to Virtual (P2V) Converter")
        self.root.geometry("1000x800")
        
        # Session logs for this execution
        self.session_logs = []
        
        # Operation control variables
        self.operation_running = False
        self.stop_requested = False
        
        # VM configuration variables
        self.vm_name = tk.StringVar(value="converted_vm")
        self.output_path = tk.StringVar(value="/tmp/p2v_output")
        
        # Configure the main window
        self.setup_window()
        
        # Create the GUI elements
        self.create_widgets()
        
        # Set up window close protocol
        self.root.protocol("WM_DELETE_WINDOW", self.exit_application)
        
        # Log the GUI initialization
        self.add_session_log("P2V Converter GUI initialized successfully")
        log_info("P2V Converter GUI initialized successfully")
        
        # Check for required tools
        self.check_prerequisites()
    
    def setup_window(self):
        """Configure the main window properties"""
        self.root.resizable(True, True)
        self.root.minsize(800, 600)
        
        # Configure grid weights for responsive design
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        
        # Set window icon (if available)
        try:
            self.root.iconname("P2V Converter")
        except:
            pass
    
    def create_widgets(self):
        """Create all GUI widgets"""
        self.create_header_frame()
        self.create_main_frame()
        self.create_status_frame()
    
    def create_header_frame(self):
        """Create the header frame with title and PDF generation buttons"""
        header_frame = ttk.Frame(self.root, padding="10")
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_columnconfigure(1, weight=1)  # Make middle column expand
        
        # Title label with icon-like symbol
        title_frame = ttk.Frame(header_frame)
        title_frame.grid(row=0, column=0, sticky="w")
        
        title_label = ttk.Label(title_frame, text="🖥️ P2V Converter", 
                               font=("Arial", 18, "bold"))
        title_label.grid(row=0, column=0, sticky="w")
        
        subtitle_label = ttk.Label(title_frame, text="Physical to Virtual Machine Converter", 
                                  font=("Arial", 9), foreground="gray")
        subtitle_label.grid(row=1, column=0, sticky="w")
        
        # Button frame for PDF generation buttons
        button_frame = ttk.Frame(header_frame)
        button_frame.grid(row=0, column=2, sticky="e")
        
        # Print session log button
        self.session_pdf_btn = ttk.Button(button_frame, 
                                         text="📄 Print Log Session",
                                         command=self.generate_session_pdf,
                                         width=18)
        self.session_pdf_btn.grid(row=0, column=0, padx=(0, 5))
        
        # Print complete log file button
        self.file_pdf_btn = ttk.Button(button_frame, 
                                      text="📋 Print Log File",
                                      command=self.generate_log_file_pdf,
                                      width=18)
        self.file_pdf_btn.grid(row=0, column=1, padx=(0, 5))
        
        # Exit button
        self.exit_btn = ttk.Button(button_frame, 
                                  text="❌ Exit",
                                  command=self.exit_application,
                                  width=12)
        self.exit_btn.grid(row=0, column=2)
        
        # Add separator
        separator = ttk.Separator(self.root, orient='horizontal')
        separator.grid(row=0, column=0, sticky="ew", pady=(0, 5))
    
    def create_main_frame(self):
        """Create the main content frame"""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        main_frame.grid_rowconfigure(4, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)
        
        # Source disk selection frame
        source_frame = ttk.LabelFrame(main_frame, text="Source Disk Selection", padding="10")
        source_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        source_frame.grid_columnconfigure(1, weight=1)
        
        ttk.Label(source_frame, text="Physical Disk:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.source_var = tk.StringVar()
        self.source_combo = ttk.Combobox(source_frame, textvariable=self.source_var, 
                                        state="readonly", font=("Arial", 9))
        self.source_combo.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self.source_combo.bind("<<ComboboxSelected>>", self.on_source_selected)
        
        # Refresh button
        self.refresh_btn = ttk.Button(source_frame, text="🔄 Refresh Disks", 
                                     command=self.refresh_disks)
        self.refresh_btn.grid(row=0, column=2, padx=(10, 0))
        
        # VM configuration frame
        vm_config_frame = ttk.LabelFrame(main_frame, text="VM Configuration", padding="10")
        vm_config_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        vm_config_frame.grid_columnconfigure(1, weight=1)
        
        # VM Name
        ttk.Label(vm_config_frame, text="VM Name:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w")
        vm_name_entry = ttk.Entry(vm_config_frame, textvariable=self.vm_name, font=("Arial", 9))
        vm_name_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        vm_name_entry.bind("<KeyRelease>", self.validate_vm_name_input)
        
        # Output Directory
        ttk.Label(vm_config_frame, text="Output Directory:", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(10, 0))
        
        output_frame = ttk.Frame(vm_config_frame)
        output_frame.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(10, 0))
        output_frame.grid_columnconfigure(0, weight=1)
        
        output_entry = ttk.Entry(output_frame, textvariable=self.output_path, font=("Arial", 9))
        output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        
        browse_btn = ttk.Button(output_frame, text="Browse...", command=self.browse_output_dir)
        browse_btn.grid(row=0, column=1)
        
        # Space information frame
        space_frame = ttk.LabelFrame(main_frame, text="Storage Space Information", padding="10")
        space_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        
        self.space_info_text = tk.Text(space_frame, height=6, wrap=tk.WORD, state=tk.DISABLED, 
                                      font=("Consolas", 9), bg="#f8f8f8")
        space_scrollbar = ttk.Scrollbar(space_frame, orient="vertical", command=self.space_info_text.yview)
        self.space_info_text.configure(yscrollcommand=space_scrollbar.set)
        
        self.space_info_text.grid(row=0, column=0, sticky="nsew")
        space_scrollbar.grid(row=0, column=1, sticky="ns")
        
        space_frame.grid_rowconfigure(0, weight=1)
        space_frame.grid_columnconfigure(0, weight=1)
        
        # Control buttons frame
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        
        self.check_space_btn = ttk.Button(control_frame, text="🔍 Check Space Requirements", 
                                         command=self.check_space_requirements)
        self.check_space_btn.grid(row=0, column=0, padx=(0, 10))
        
        self.convert_btn = ttk.Button(control_frame, text="▶️ Start P2V Conversion", 
                                     command=self.start_conversion, style="Accent.TButton")
        self.convert_btn.grid(row=0, column=1, padx=(0, 10))
        
        self.stop_btn = ttk.Button(control_frame, text="⏹️ Stop Operation", 
                                  command=self.stop_operation, state=tk.DISABLED)
        self.stop_btn.grid(row=0, column=2, padx=(0, 10))
        
        self.clear_log_btn = ttk.Button(control_frame, text="🗑️ Clear Log", 
                                       command=self.clear_log)
        self.clear_log_btn.grid(row=0, column=3)
        
        # Progress and log area
        log_frame = ttk.LabelFrame(main_frame, text="Operation Log", padding="5")
        log_frame.grid(row=4, column=0, sticky="nsew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        
        # Create text widget with scrollbar
        text_frame = ttk.Frame(log_frame)
        text_frame.grid(row=0, column=0, sticky="nsew")
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)
        
        self.log_text = tk.Text(text_frame, wrap=tk.WORD, state=tk.DISABLED, 
                               font=("Consolas", 9), bg="#f8f8f8", fg="#333333")
        scrollbar_v = ttk.Scrollbar(text_frame, orient="vertical", command=self.log_text.yview)
        scrollbar_h = ttk.Scrollbar(text_frame, orient="horizontal", command=self.log_text.xview)
        
        self.log_text.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)
        
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar_v.grid(row=0, column=1, sticky="ns")
        scrollbar_h.grid(row=1, column=0, sticky="ew")
        
        # Configure text tags for different log levels
        self.log_text.tag_configure("INFO", foreground="#0066cc")
        self.log_text.tag_configure("WARNING", foreground="#ff6600")
        self.log_text.tag_configure("ERROR", foreground="#cc0000")
        self.log_text.tag_configure("SUCCESS", foreground="#009900")
    
    def create_status_frame(self):
        """Create the status frame at the bottom"""
        status_frame = ttk.Frame(self.root, padding="10")
        status_frame.grid(row=2, column=0, sticky="ew")
        status_frame.grid_columnconfigure(1, weight=1)
        
        # Progress bar with label
        progress_label = ttk.Label(status_frame, text="Progress:")
        progress_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(status_frame, variable=self.progress_var, 
                                           maximum=100, length=300)
        self.progress_bar.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        
        # Progress percentage label
        self.progress_label = ttk.Label(status_frame, text="0%")
        self.progress_label.grid(row=0, column=2, sticky="w", padx=(0, 20))
        
        # Status label
        status_info_label = ttk.Label(status_frame, text="Status:")
        status_info_label.grid(row=0, column=3, sticky="w", padx=(0, 10))
        
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, 
                                     font=("Arial", 9, "bold"))
        self.status_label.grid(row=0, column=4, sticky="w")
        
        # Current operation details
        self.operation_details = ttk.Label(status_frame, text="", 
                                          font=("Arial", 8), foreground="gray")
        self.operation_details.grid(row=1, column=0, columnspan=5, sticky="w", pady=(5, 0))
        
        # Initialize with disk refresh
        self.root.after(100, self.refresh_disks)  # Delayed initialization
    
    def check_prerequisites(self):
        """Check if required tools are available"""
        tools_available, message = check_qemu_tools()
        if not tools_available:
            self.add_session_log(f"Prerequisites check failed: {message}", "ERROR")
            messagebox.showerror("Missing Prerequisites", 
                               f"❌ Required tools are missing:\n\n{message}\n\n"
                               f"Please install the required packages:\n"
                               f"• qemu-utils (for qemu-img)\n"
                               f"• coreutils (for dd)")
        else:
            self.add_session_log("All prerequisites are available", "SUCCESS")
    
    def add_session_log(self, message, level="INFO"):
        """Add a message to the session logs list"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"{timestamp} - {level} - {message}"
        self.session_logs.append(formatted_message)
        
        # Also display in the GUI log
        self.update_log_display(f"[{level}] {message}", level)
    
    def update_log_display(self, message, level="INFO"):
        """Update the log display in the GUI"""
        self.log_text.config(state=tk.NORMAL)
        
        # Insert timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Insert message with appropriate tag
        self.log_text.insert(tk.END, f"{timestamp} ", "INFO")
        self.log_text.insert(tk.END, f"{message}\n", level)
        
        # Auto-scroll to bottom
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        
        # Update GUI
        self.root.update_idletasks()
    
    def clear_log(self):
        """Clear the log display"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
        self.add_session_log("Log display cleared")
    
    def refresh_disks(self):
        """Refresh the list of available disks"""
        try:
            self.add_session_log("Refreshing disk list...")
            log_info("Refreshing disk list")
            
            # Get list of disks
            disks = get_disk_list()
            
            if disks:
                # Format disk info for display
                disk_options = []
                for disk in disks:
                    disk_info = f"{disk['device']} ({disk['size']}) - {disk['model']}"
                    disk_options.append(disk_info)
                
                # Update combobox
                self.source_combo['values'] = disk_options
                
                # Clear previous selection if it's no longer valid
                if self.source_var.get() not in disk_options:
                    self.source_var.set("")
                
                # Highlight system disk
                active_disks = get_active_disk()
                if active_disks:
                    for i, disk_info in enumerate(disk_options):
                        for active_disk in active_disks:
                            if f"/dev/{active_disk}" in disk_info:
                                # Mark as system disk
                                disk_options[i] = f"🟡 {disk_info} [SYSTEM DISK]"
                                break
                    self.source_combo['values'] = disk_options
                
                self.add_session_log(f"Found {len(disks)} disk(s)", "SUCCESS")
                self.status_var.set(f"Found {len(disks)} disk(s)")
                
            else:
                self.add_session_log("No disks found", "WARNING")
                self.status_var.set("No disks found")
                
        except Exception as e:
            error_msg = f"Error refreshing disks: {str(e)}"
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            messagebox.showerror("Error", error_msg)
    
    def on_source_selected(self, event=None):
        """Handle source disk selection"""
        selected = self.source_var.get()
        if selected:
            # Extract device path
            device_path = selected.split(' ')[0].replace('🟡 ', '')
            self.add_session_log(f"Selected source disk: {device_path}")
            
            # Auto-update VM name based on disk
            disk_name = device_path.split('/')[-1]  # e.g., sda from /dev/sda
            self.vm_name.set(f"{disk_name}_vm")
    
    def validate_vm_name_input(self, event=None):
        """Validate VM name as user types"""
        name = self.vm_name.get()
        is_valid, message = validate_vm_name(name)
        
        if not is_valid and name:  # Only show error if there's content
            self.operation_details.config(text=f"⚠️ {message}", foreground="red")
        else:
            self.operation_details.config(text="", foreground="gray")
    
    def browse_output_dir(self):
        """Browse for output directory"""
        selected_dir = filedialog.askdirectory(
            title="Select Output Directory for VM Files",
            initialdir=self.output_path.get()
        )
        
        if selected_dir:
            self.output_path.set(selected_dir)
            self.add_session_log(f"Output directory selected: {selected_dir}")
    
    def check_space_requirements(self):
        """Check space requirements and display information"""
        try:
            source = self.source_var.get()
            output_dir = self.output_path.get()
            
            if not source:
                messagebox.showwarning("Warning", "Please select a source disk first")
                return
            
            if not output_dir:
                messagebox.showwarning("Warning", "Please specify an output directory")
                return
            
            # Extract device path and get disk info
            device_path = source.split(' ')[0].replace('🟡 ', '')
            
            # Get disk size
            try:
                result = subprocess.run(['blockdev', '--getsize64', device_path], 
                                      capture_output=True, text=True, check=True)
                disk_size = int(result.stdout.strip())
            except Exception as e:
                raise Exception(f"Could not determine disk size: {str(e)}")
            
            # Check space
            has_space, space_message = check_output_space(output_dir, disk_size)
            
            # Update space info display
            self.space_info_text.config(state=tk.NORMAL)
            self.space_info_text.delete(1.0, tk.END)
            
            info_text = f"Source Disk: {device_path}\n"
            info_text += f"Source Size: {format_bytes(disk_size)}\n"
            info_text += f"Output Directory: {output_dir}\n\n"
            info_text += space_message
            
            self.space_info_text.insert(tk.END, info_text)
            self.space_info_text.config(state=tk.DISABLED)
            
            if has_space:
                self.add_session_log("Space check passed - sufficient space available", "SUCCESS")
            else:
                self.add_session_log("Space check failed - insufficient space", "ERROR")
                messagebox.showwarning("Insufficient Space", 
                                     f"⚠️ Not enough space available!\n\n{space_message}")
            
        except Exception as e:
            error_msg = f"Error checking space requirements: {str(e)}"
            self.add_session_log(error_msg, "ERROR")
            messagebox.showerror("Error", error_msg)
    
    def start_conversion(self):
        """Start the P2V conversion operation"""
        source = self.source_var.get()
        vm_name = self.vm_name.get().strip()
        output_dir = self.output_path.get().strip()
        
        # Validation
        if not source:
            messagebox.showwarning("Warning", "Please select a source disk")
            return
        
        if not vm_name:
            messagebox.showwarning("Warning", "Please enter a VM name")
            return
        
        if not output_dir:
            messagebox.showwarning("Warning", "Please specify an output directory")
            return
        
        # Validate VM name
        is_valid, validation_message = validate_vm_name(vm_name)
        if not is_valid:
            messagebox.showerror("Invalid VM Name", validation_message)
            return
        
        # Extract device path
        device_path = source.split(' ')[0].replace('🟡 ', '')
        
        # Check if converting system disk
        if "SYSTEM DISK" in source:
            result = messagebox.askyesno("System Disk Warning", 
                                       f"⚠️ WARNING ⚠️\n\n"
                                       f"You are about to convert the system disk:\n"
                                       f"{device_path}\n\n"
                                       f"This operation will:\n"
                                       f"• Read all data from the active system disk\n"
                                       f"• May affect system performance during conversion\n"
                                       f"• Create a complete copy as a virtual machine\n\n"
                                       f"Are you sure you want to continue?")
            if not result:
                return
        
        # Final confirmation
        if not messagebox.askyesno("Confirm P2V Conversion", 
                                  f"🖥️ P2V Conversion Confirmation\n\n"
                                  f"Source Disk: {device_path}\n"
                                  f"VM Name: {vm_name}\n"
                                  f"Output Directory: {output_dir}\n\n"
                                  f"This will create a compressed qcow2 virtual machine.\n"
                                  f"The process may take a significant amount of time.\n\n"
                                  f"Continue with the conversion?"):
            return
        
        # Start conversion in a separate thread
        self.operation_running = True
        self.stop_requested = False
        
        conversion_thread = threading.Thread(target=self._conversion_worker, 
                                            args=(device_path, output_dir, vm_name))
        conversion_thread.daemon = True
        conversion_thread.start()
        
        # Update UI
        self.convert_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.refresh_btn.config(state=tk.DISABLED)
        self.check_space_btn.config(state=tk.DISABLED)
        self.status_var.set("P2V conversion in progress...")
    
    def _conversion_worker(self, source_device, output_dir, vm_name):
        """Worker thread for P2V conversion operation"""
        try:
            self.add_session_log(f"Starting P2V conversion: {source_device} -> {vm_name}.qcow2")
            log_info(f"P2V conversion started: {source_device} -> {vm_name}")
            
            def progress_callback(percent, status):
                self.root.after(0, lambda: self._update_progress(percent, status))
            
            def stop_check():
                return self.stop_requested
            
            # Perform the conversion
            output_file = create_vm_from_disk(source_device, output_dir, vm_name, 
                                            progress_callback, stop_check)
            
            if not self.stop_requested:
                self.add_session_log("P2V conversion completed successfully", "SUCCESS")
                log_info("P2V conversion completed successfully")
                
                # Get final file size
                final_size = os.path.getsize(output_file)
                self.add_session_log(f"VM created: {output_file} ({format_bytes(final_size)})", "SUCCESS")
                
                # Show completion dialog
                self.root.after(0, lambda: messagebox.showinfo("Conversion Complete", 
                    f"✅ P2V conversion completed successfully!\n\n"
                    f"VM File: {output_file}\n"
                    f"Size: {format_bytes(final_size)}\n\n"
                    f"You can now use this qcow2 file with QEMU/KVM or other\n"
                    f"virtualization platforms that support qcow2 format."))
            
        except KeyboardInterrupt:
            self.add_session_log("P2V conversion cancelled by user", "WARNING")
            log_warning("P2V conversion cancelled by user")
        except Exception as e:
            error_msg = f"P2V conversion failed: {str(e)}"
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            self.root.after(0, lambda: messagebox.showerror("Conversion Failed", 
                f"❌ P2V conversion failed:\n\n{error_msg}"))
        
        finally:
            # Reset UI in main thread
            self.root.after(0, self._reset_ui_after_operation)
    
    def _update_progress(self, percent, status):
        """Update progress bar and status from worker thread"""
        self.progress_var.set(percent)
        self.progress_label.config(text=f"{percent:.1f}%")
        self.operation_details.config(text=status, foreground="blue")
    
    def _reset_ui_after_operation(self):
        """Reset UI after operation completes"""
        self.operation_running = False
        self.convert_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.refresh_btn.config(state=tk.NORMAL)
        self.check_space_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.progress_label.config(text="0%")
        self.status_var.set("Ready")
        self.operation_details.config(text="", foreground="gray")
    
    def stop_operation(self):
        """Stop the current operation"""
        if self.operation_running:
            self.stop_requested = True
            self.add_session_log("Stop requested by user", "WARNING")
            log_warning("Stop requested by user")
            self.status_var.set("Stopping...")
    
    def exit_application(self):
        """Exit the application with confirmation"""
        if self.operation_running:
            result = messagebox.askyesno("Exit Confirmation", 
                                       "⚠️ An operation is currently running.\n\n"
                                       "Are you sure you want to exit?\n"
                                       "This will stop the current operation.")
            if result:
                self.stop_requested = True
                self.add_session_log("Application exit requested during operation", "WARNING")
                log_warning("Application exit requested during operation")
                # Give a moment for the operation to stop
                self.root.after(1000, self._force_exit)
            return
        
        # Normal exit confirmation
        result = messagebox.askyesno("Exit Confirmation", 
                                   "Are you sure you want to exit the Disk Cloner?")
        if result:
            self.add_session_log("Application exit requested by user")
            log_info("Disk Cloner application terminated by user")
            self.root.quit()
            self.root.destroy()
    
    def _force_exit(self):
        """Force exit after stopping operation"""
        self.root.quit()
        self.root.destroy()
    
    def generate_session_pdf(self):
        """Generate PDF from current session logs"""
        try:
            self.add_session_log("Generating session log PDF...")
            
            # Disable button during generation
            self.session_pdf_btn.config(state=tk.DISABLED)
            self.status_var.set("Generating PDF...")
            
            # Generate PDF
            pdf_path = generate_session_pdf(self.session_logs)
            
            # Show success message without option to open
            messagebox.showinfo("PDF Generated", 
                               f"📄 Session log PDF generated successfully!\n\n"
                               f"Location: {pdf_path}")
            
            self.add_session_log(f"Session PDF generated: {pdf_path}", "SUCCESS")
            
        except Exception as e:
            error_msg = f"Failed to generate session PDF: {str(e)}"
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            messagebox.showerror("PDF Generation Error", 
                               f"❌ Failed to generate PDF:\n\n{error_msg}")
        
        finally:
            # Re-enable button and reset status
            self.session_pdf_btn.config(state=tk.NORMAL)
            self.status_var.set("Ready")
    
    def generate_log_file_pdf(self):
        """Generate PDF from complete log file"""
        try:
            self.add_session_log("Generating complete log file PDF...")
            
            # Disable button during generation
            self.file_pdf_btn.config(state=tk.DISABLED)
            self.status_var.set("Generating PDF...")
            
            # Generate PDF
            pdf_path = generate_log_file_pdf()
            
            # Show success message without option to open
            messagebox.showinfo("PDF Generated", 
                               f"📋 Complete log file PDF generated successfully!\n\n"
                               f"Location: {pdf_path}")
            
            self.add_session_log(f"Complete log PDF generated: {pdf_path}", "SUCCESS")
            
        except Exception as e:
            error_msg = f"Failed to generate log file PDF: {str(e)}"
            self.add_session_log(error_msg, "ERROR")
            log_error(error_msg)
            messagebox.showerror("PDF Generation Error", 
                               f"❌ Failed to generate PDF:\n\n{error_msg}")
        
        finally:
            # Re-enable button and reset status
            self.file_pdf_btn.config(state=tk.NORMAL)
            self.status_var.set("Ready")