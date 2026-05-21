import os
import re

addon_dir = '/home/ragulg/Documents/odoo17/addons/dropbox_odoo_integration'

# 1. Collect files to rename
rename_map = {}
for root, dirs, files in os.walk(addon_dir):
    if '__pycache__' in root or '.git' in root:
        continue
    for name in files:
        if 'one_drive' in name or 'onedrive' in name:
            old_path = os.path.join(root, name)
            new_name = name.replace('one_drive', 'dropbox').replace('onedrive', 'dropbox')
            new_path = os.path.join(root, new_name)
            rename_map[old_path] = new_path

# Create replacements map for file contents
replacements = []
for old_path, new_path in rename_map.items():
    old_name = os.path.basename(old_path)
    new_name = os.path.basename(new_path)
    # Replace exact filename
    replacements.append((old_name, new_name))
    
    # If it's a python file, we also need to replace the module import name
    if old_name.endswith('.py') and old_name != '__init__.py':
        old_mod = old_name[:-3]
        new_mod = new_name[:-3]
        replacements.append((f"import {old_mod}", f"import {new_mod}"))
        replacements.append((f"from . import {old_mod}", f"from . import {new_mod}"))

# Sort replacements by length descending to avoid partial replacements
replacements.sort(key=lambda x: len(x[0]), reverse=True)

# 2. Rename the files
for old_path, new_path in rename_map.items():
    print(f"Renaming {old_path} -> {new_path}")
    os.rename(old_path, new_path)

# 3. Update contents in all files
for root, dirs, files in os.walk(addon_dir):
    if '__pycache__' in root or '.git' in root:
        continue
    for name in files:
        if not name.endswith(('.py', '.xml', '.csv', '.js', '.css', '.md', '.txt')):
            continue
        file_path = os.path.join(root, name)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            continue
            
        new_content = content
        for old_str, new_str in replacements:
            new_content = new_content.replace(old_str, new_str)
            
        if new_content != content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Updated contents of {file_path}")

print("Done")
