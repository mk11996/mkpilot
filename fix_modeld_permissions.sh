#!/bin/bash
# Fix modeld executable permissions
# Run this script on the device to fix the commIssue error

echo "Fixing modeld executable permissions..."

# Make _modeld executable in hybrid_modeld directory
chmod +x /data/openpilot/selfdrive/hybrid_modeld/_modeld
echo "✓ Fixed hybrid_modeld/_modeld permissions"

# Make _modeld executable in legacy_modeld directory
chmod +x /data/openpilot/selfdrive/legacy_modeld/_modeld
echo "✓ Fixed legacy_modeld/_modeld permissions"

# Also make _dmonitoringmodeld executable if it exists
if [ -f /data/openpilot/selfdrive/hybrid_modeld/_dmonitoringmodeld ]; then
    chmod +x /data/openpilot/selfdrive/hybrid_modeld/_dmonitoringmodeld
    echo "✓ Fixed hybrid_modeld/_dmonitoringmodeld permissions"
fi

if [ -f /data/openpilot/selfdrive/legacy_modeld/_dmonitoringmodeld ]; then
    chmod +x /data/openpilot/selfdrive/legacy_modeld/_dmonitoringmodeld
    echo "✓ Fixed legacy_modeld/_dmonitoringmodeld permissions"
fi

echo ""
echo "All permissions fixed! You can now restart openpilot."
echo "The modeld process should start correctly now."
