# -*- coding: utf-8 -*-

import base64
import io
import csv
import zipfile
import xml.etree.ElementTree as ET
from odoo import models, fields, _, api
from odoo.exceptions import UserError


class WhatsAppImportWizard(models.TransientModel):
    _name = 'whatsapp.import.wizard'
    _description = 'Import Mobile Numbers from Excel/CSV'

    file_data = fields.Binary('Excel/CSV File', required=True)
    file_name = fields.Char('File Name')

    def _read_xlsx_native(self, file_content):
        """Read .xlsx file using Python's built-in zipfile and xml (no external libs needed)"""
        try:
            # xlsx files are ZIP archives containing XML files
            with zipfile.ZipFile(io.BytesIO(file_content)) as z:
                # Read shared strings (text values are stored separately)
                shared_strings = []
                if 'xl/sharedStrings.xml' in z.namelist():
                    with z.open('xl/sharedStrings.xml') as f:
                        tree = ET.parse(f)
                        ns = {'ns': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                        for si in tree.findall('.//ns:si', ns):
                            t = si.find('.//ns:t', ns)
                            shared_strings.append(t.text if t is not None and t.text else '')
                
                # Read the first worksheet
                with z.open('xl/worksheets/sheet1.xml') as f:
                    tree = ET.parse(f)
                    ns = {'ns': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                    
                    rows_data = []
                    for row in tree.findall('.//ns:row', ns):
                        row_values = []
                        for cell in row.findall('ns:c', ns):
                            cell_value = ''
                            v = cell.find('ns:v', ns)
                            if v is not None and v.text:
                                # Check if it's a shared string reference
                                if cell.get('t') == 's':
                                    idx = int(v.text)
                                    if idx < len(shared_strings):
                                        cell_value = shared_strings[idx]
                                else:
                                    cell_value = v.text
                            row_values.append(cell_value)
                        rows_data.append(row_values)
                    
                    return rows_data
        except Exception as e:
            raise UserError(_("Could not read XLSX file: %s") % str(e))

    def _read_csv_file(self, file_content):
        """Read .csv file"""
        try:
            try:
                content = file_content.decode('utf-8')
            except:
                content = file_content.decode('latin-1')
            
            reader = csv.reader(io.StringIO(content))
            return list(reader)
        except Exception as e:
            raise UserError(_("Could not read CSV file: %s") % str(e))

    def _extract_numbers_from_rows(self, rows):
        """Extract mobile numbers from parsed rows"""
        if not rows:
            raise UserError(_("The file is empty."))
        
        # Find mobile_numbers column in header
        header_row = [str(cell).strip().lower() for cell in rows[0]]
        target_col_index = -1
        
        for index, col_name in enumerate(header_row):
            if col_name == 'mobile_numbers':
                target_col_index = index
                break
        
        if target_col_index == -1:
            raise UserError(_("Column 'mobile_numbers' not found in the first row."))
        
        # Extract numbers from data rows
        numbers = []
        for row in rows[1:]:
            if row and len(row) > target_col_index:
                raw_val = row[target_col_index]
                if raw_val:
                    # Handle numeric values
                    try:
                        if '.' in str(raw_val):
                            str_val = str(int(float(raw_val)))
                        else:
                            str_val = str(raw_val).strip()
                    except:
                        str_val = str(raw_val).strip()
                    
                    if str_val:
                        numbers.append(str_val)
        
        return numbers

    def action_import_apply(self):
        """Parse Excel/CSV and update the active record"""
        self.ensure_one()

        # 1. Validation
        if not self.file_name:
            raise UserError(_("Please upload a file."))
        
        file_ext = self.file_name.lower().split('.')[-1] if '.' in self.file_name else ''
        
        if file_ext not in ('xlsx', 'csv'):
            raise UserError(_("Please upload a valid file (.xlsx or .csv). Note: .xls format is not supported."))

        # 2. Decode the file
        try:
            file_content = base64.b64decode(self.file_data)
        except Exception as e:
            raise UserError(_("Could not decode the file: %s") % str(e))

        # 3. Read file based on extension
        if file_ext == 'xlsx':
            rows = self._read_xlsx_native(file_content)
        elif file_ext == 'csv':
            rows = self._read_csv_file(file_content)
        else:
            raise UserError(_("Unsupported file format."))

        # 4. Extract numbers from rows
        raw_numbers = self._extract_numbers_from_rows(rows)

        # 5. Validate Numbers (must start with +)
        valid_numbers = []
        for str_val in raw_numbers:
            if str_val.startswith('+'):
                clean_num = str_val.replace(" ", "").replace("-", "")
                valid_numbers.append(clean_num)

        if not valid_numbers:
            raise UserError(_("No valid numbers found. Make sure numbers start with a country code (e.g., +91)."))

        # 6. Update the Main Record
        active_id = self.env.context.get('active_id')
        active_model = self.env.context.get('active_model')

        if active_id and active_model:
            parent_record = self.env[active_model].browse(active_id)
            final_string = ", ".join(valid_numbers)
            parent_record.write({'recipient_multi': final_string})

        return {'type': 'ir.actions.act_window_close'}
