
  Axion by 4Labs — macOS Package
  =================================

  CONTENTS
  --------
  axion/              The complete Axion application folder

  HOW TO TEST
  -----------
  1. Copy the "axion" folder to your home directory: ~/axion

  2. OPTION A — Run the installer script (recommended for first use)
     Open Terminal and run:
       cd ~/axion
       chmod +x scripts/install-mac.sh
       ./scripts/install-mac.sh

     This will:
     - Install Python 3.12 via Homebrew (if needed)
     - Create a virtual environment
     - Install all dependencies
     - Set up auto-start on login
     - Open the dashboard in your browser

  3. OPTION B — Double-click Axion.app
     This launches the menu bar app. Look for "A" in the menu bar.
     The server starts automatically on first launch.
     NOTE: Axion.app is a launcher that needs the rest of the
     axion folder (src/, config/, dashboard/, etc.) to be present.

  4. Open http://localhost:7777 in your browser to see the dashboard.

  REQUIREMENTS
  ------------
  - macOS 12+ (Monterey or later)
  - Internet access for first-run dependency installation
  - Internet access for RSS news collection

  WHAT TO EXPECT
  --------------
  - First launch: 1-2 minutes for auto-setup
  - Dashboard shows "Welcome to Axion" setup guide
  - Upload sample_portfolio.csv to see holdings, then wait for news collection
  - Menu bar icon "A" shows server status

  NOTES
  -----
  - Axion.app is not code-signed. macOS Gatekeeper may block it.
    Right-click Axion.app, select "Open", then click "Open" in the dialog.
    Or: System Settings > Privacy & Security > Allow.
  - Data is stored at: ~/kleitos-data/
  - Settings are stored at: ~/.axion.env
  - Auto-start uses launchd (com.axion.app)

