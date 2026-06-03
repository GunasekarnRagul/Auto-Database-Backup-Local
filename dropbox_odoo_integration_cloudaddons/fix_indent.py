import re

with open('models/dropbox_dashboard.py', 'r') as f:
    lines = f.readlines()

out = []
in_method = False
method_name = ""
skip = False

METHODS_TO_WRAP = {
    'get_kpi_data': "{'kpis': {}, 'health': {'fleet': []}}",
    'get_storage_stats': "{'storage_saved_formatted': '0 B', 'drive_space_used_formatted': '0 B'}",
    'get_activity_logs': "[]",
    'get_duplicate_summary': "{'duplicate_count': 0}",
    'get_sync_trend_data': "{'labels': [], 'success': [], 'failed': []}",
    'get_model_breakdown': "[]",
    'get_error_summary': "{'fails_24h': 0, 'fails_7d': 0, 'top_errors': []}",
    'get_files_by_model': "[]",
    'get_top_uploaders': "[]",
    'get_top_file_types': "[]",
    'get_largest_files': "[]",
    'get_recent_files': "[]",
    'get_business_model_counts': "[]",
    'get_storage_by_model': "{'labels': [], 'data': []}",
    'get_orphan_attachments': "{'orphan_count': 0}",
}

i = 0
while i < len(lines):
    line = lines[i]
    
    match = re.match(r'^    def (get_[a-zA-Z_]+)\(', line)
    if match and match.group(1) in METHODS_TO_WRAP:
        method_name = match.group(1)
        # Add the def line
        out.append(line)
        i += 1
        
        # Add docstring if it exists on next line
        if i < len(lines) and '"""' in lines[i]:
            out.append(lines[i])
            i += 1
            
        out.append("        try:\n")
        
        # Now collect the body and indent it by 4 spaces
        while i < len(lines):
            next_line = lines[i]
            # If we hit an unindented line or next method def (at 4 spaces), break
            if next_line.strip() and not next_line.startswith('        '):
                if next_line.startswith('    def ') or next_line.startswith('    @api'):
                    break
            
            # If the line is empty or just spaces, keep it
            if not next_line.strip():
                out.append(next_line)
            else:
                out.append("    " + next_line)
            i += 1
            
        # Add the except block
        out.append("        except Exception as e:\n")
        out.append(f'            _logger.exception("Error in {method_name}: %s", e)\n')
        out.append(f'            return {METHODS_TO_WRAP[method_name]}\n\n')
        continue

    out.append(line)
    i += 1

with open('models/dropbox_dashboard.py', 'w') as f:
    f.writelines(out)

