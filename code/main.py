#!/usr/bin/env python3

import os
import sys
import tkinter as tk
from gui import P2VConverterGUI
from log_handler import log_info

def main():
    """Main function to run the P2V converter"""
    # Check for root privileges
    if os.geteuid() != 0:
        print("This program must be run as root!")
        sys.exit(1)
    
    log_info("P2V Converter application started")
    
    root = tk.Tk()
    app = P2VConverterGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()