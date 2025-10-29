import pdfplumber
import json
import base64
from pathlib import Path
from typing import Dict, List, Any
import io
import fitz  # PyMuPDF
import re
from datetime import datetime


# ============================================================
#  UNIFIED COORDINATE SYSTEM
# ============================================================
class CoordinateConverter:
    """Convert all coordinates to consistent system (Y=0 at bottom)"""
    
    def __init__(self, page_height=842):
        self.page_height = page_height
    
    def pdfplumber_to_standard(self, y):
        """Convert pdfplumber coordinates (Y=0 at top) to standard (Y=0 at bottom)"""
        return self.page_height - y
    
    def pymupdf_to_standard(self, y, height=0):
        """Convert PyMuPDF coordinates to standard"""
        return self.page_height - y - height


# ============================================================
#  ENHANCED TEXT CLEANING
# ============================================================
def enhanced_clean_extracted_text(text: str) -> str:
    """Comprehensive OCR correction while preserving special content"""
    if not text:
        return text
    
    # Preserve Telugu text - DO NOT modify
    telugu_chars = re.findall(r'[\u0C00-\u0C7F]', text)
    if telugu_chars:
        return text
    
    # Enhanced OCR corrections
    replacements = {
        'wi√': 'will', 'fi√': 'fill', 'studesnts': 'students',
        'shk': 'CS', '√': 'square root', '÷': '÷', '×': '×',
        '²': '²', '³': '³', '°': '°', 'ﬁ': 'fi', 'ﬂ': 'fl'
    }
    
    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)
    
    # Fix common OCR issues with regex
    text = re.sub(r'(\d)\s+(\d)', r'\1\2', text)  # Fix spaced numbers
    text = re.sub(r'(\w)-\s+(\w)', r'\1-\2', text)  # Fix hyphenated words
    
    return text.strip()


def merge_fragmented_text_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge single-character text blocks into proper words"""
    if not blocks:
        return blocks
    
    merged_blocks = []
    current_block = None
    
    for block in blocks:
        content = block.get('content', '').strip()
        
        # Check if this is a fragmented character (single char with small font)
        is_fragment = (len(content) == 1 and 
                      block.get('font', {}).get('size', 12) < 8 and
                      content.isalpha())
        
        if is_fragment:
            if current_block:
                # Merge with current block
                current_block['content'] += content
                # Update position to include the merged character
                current_block['position']['x1'] = block['position']['x1']
                current_block['position']['width'] = current_block['position']['x1'] - current_block['position']['x0']
                current_block['word_count'] = len(current_block['content'].split())
            else:
                # Start new merged block
                current_block = block.copy()
        else:
            # Non-fragment block
            if current_block:
                merged_blocks.append(current_block)
                current_block = None
            merged_blocks.append(block)
    
    # Don't forget the last merged block
    if current_block:
        merged_blocks.append(current_block)
    
    return merged_blocks


def improved_content_classification(text: str) -> str:
    """Better content classification for government exam papers"""
    # Check for Telugu text first (preserve it)
    telugu_chars = re.findall(r'[\u0C00-\u0C7F]', text)
    if telugu_chars:
        return "telugu_text"
    
    text_lower = text.lower().strip()
    
    # Instructions
    if any(word in text_lower for word in ['directions', 'study the', 'answer the', 'following questions', 'instruction']):
        return "instruction"
    
    # Questions (starts with number and period)
    elif re.match(r'^\d+\.', text.strip()):
        return "question"
    
    # Options (starts with number and parenthesis)
    elif re.match(r'^[1-5]\)', text.strip()):
        return "option"
    
    # Data labels (years, numbers on axes)
    elif re.match(r'^(201[2-9]|202[0-9]|60|50|40|30|20|10|0)$', text.strip()):
        return "data_label"
    
    # Headers (all caps or title case)
    elif re.match(r'^[A-Z][A-Z\s]+$', text.strip()) or text_lower in ['numerical ability', 'years', 'question', 'answer']:
        return "header"
    
    # Mathematical content
    elif any(symbol in text for symbol in ['÷', '×', '=', '%', '√', '²', '³', 'square root']):
        return "mathematical"
    
    # Exam names and acronyms
    elif any(acronym in text for acronym in ['SBI', 'LIC', 'NIACL', 'MTS', 'CGL', 'CHSL', 'RRB', 'IBPS']):
        return "exam_reference"
    
    else:
        return "normal_text"


# ============================================================
#  ENHANCED BAR CHART DETECTION
# ============================================================
class EnhancedBarChartDetector:
    def __init__(self):
        self.chart_keywords = [
            'bar chart', 'students registered', 'exams', 'years', 'data interpretation',
            'vertical bar', 'horizontal bar', 'column chart', 'graph', 'diagram'
        ]
    
    def detect_bar_chart_enhanced(self, image_info, page_text_blocks, page_num):
        """Enhanced bar chart detection with contextual analysis"""
        detection_score = 0
        detection_features = []
        
        # Feature 1: Image dimension analysis (30% weight)
        width = image_info['dimensions']['width']
        height = image_info['dimensions']['height']
        aspect_ratio = width / height if height > 0 else 0
        
        if 1.2 <= aspect_ratio <= 3.0:  # Typical bar chart ratios
            detection_score += 0.3
            detection_features.append('optimal_aspect_ratio')
        
        # Feature 2: Contextual text analysis (40% weight)
        contextual_score = self._analyze_surrounding_context(page_text_blocks)
        detection_score += contextual_score * 0.4
        if contextual_score > 0.5:
            detection_features.append('strong_contextual_evidence')
        
        # Feature 3: Size-based confidence (30% weight)
        if width > 300 and height > 150:  # Significant chart size
            detection_score += 0.3
            detection_features.append('adequate_size')
        
        # Feature 4: Page position analysis (bonus)
        if self._is_typical_chart_position(image_info['position']):
            detection_score += 0.1
            detection_features.append('typical_position')
        
        return {
            'is_bar_chart_candidate': detection_score >= 0.6,
            'confidence': min(detection_score, 1.0),
            'detection_features': detection_features,
            'chart_type': self._classify_chart_type(aspect_ratio, contextual_score),
            'extraction_priority': 'high' if detection_score > 0.7 else 'medium'
        }
    
    def _analyze_surrounding_context(self, text_blocks):
        """Analyze surrounding text for chart indicators"""
        context_score = 0
        relevant_text = ""
        
        for block in text_blocks:
            content = block.get('content', '').lower()
            relevant_text += " " + content
            
            # Direct chart mentions
            if any(keyword in content for keyword in self.chart_keywords):
                context_score += 0.3
            
            # Data interpretation patterns
            if any(pattern in content for pattern in ['study the', 'following data', 'answer the questions']):
                context_score += 0.2
            
            # Year patterns (common in charts)
            if re.search(r'20[0-9]{2}', content):
                context_score += 0.1
        
        # Check for exam acronyms in context
        if any(acronym in relevant_text for acronym in ['mts', 'cgl', 'chsl']):
            context_score += 0.2
        
        return min(context_score, 1.0)
    
    def _is_typical_chart_position(self, position):
        """Check if image is in typical chart position"""
        x0, y0 = position.get('x0', 0), position.get('y0', 0)
        # Charts are typically centered or top-half of page
        return x0 > 100 and y0 > 200
    
    def _classify_chart_type(self, aspect_ratio, contextual_score):
        """Classify chart type based on features"""
        if aspect_ratio > 2.0:
            return "vertical_bar_chart"
        elif aspect_ratio < 1.0:
            return "horizontal_bar_chart"
        else:
            return "grouped_bar_chart"


def extract_axis_labels_from_context(page_text_blocks, chart_position):
    """Extract axis labels from surrounding text blocks"""
    axis_data = {'x_labels': [], 'y_labels': [], 'title': ''}
    
    for block in page_text_blocks:
        block_pos = block.get('position', {})
        content = block.get('content', '').strip()
        
        # Check if text block is near the chart (simplified proximity check)
        chart_x, chart_y = chart_position.get('x0', 0), chart_position.get('y0', 0)
        block_x, block_y = block_pos.get('x0', 0), block_pos.get('y0', 0)
        
        if abs(block_x - chart_x) < 200 and abs(block_y - chart_y) < 200:
            
            # Extract X-axis labels (years: 2012, 2013, etc.)
            year_matches = re.findall(r'20[0-9]{2}', content)
            if year_matches:
                axis_data['x_labels'] = sorted(set(year_matches))
                print(f"📅 Extracted X-axis years: {axis_data['x_labels']}")
            
            # Extract Y-axis labels (numbers: 0, 10, 20, etc.)
            number_matches = re.findall(r'\b\d{1,3}\b', content)
            if len(number_matches) >= 3 and all(int(n) % 10 == 0 for n in number_matches[:3]):
                axis_data['y_labels'] = sorted(set(int(n) for n in number_matches))
                print(f"📊 Extracted Y-axis scale: {axis_data['y_labels']}")
            
            # Extract chart title
            if len(content) > 20 and len(content) < 100 and not content.startswith('1)'):
                axis_data['title'] = content
                print(f"🏷️ Extracted chart title: {content[:50]}...")
    
    return axis_data


# ============================================================
#  ENHANCED ERROR HANDLING
# ============================================================
class GracefulDegradation:
    def __init__(self):
        self.fallback_strategies = {
            'text_extraction': self._fallback_text_extraction,
            'image_processing': self._fallback_image_processing,
            'table_parsing': self._fallback_table_parsing
        }
        self.error_log = []
    
    def handle_extraction_error(self, operation, error, context):
        """Comprehensive error handling with graceful degradation"""
        error_id = f"ERR_{hash(str(error))[:8]}"
        
        # Log error with context
        self._log_error(error_id, operation, error, context)
        
        # Try fallback strategy
        fallback_result = self._attempt_fallback(operation, context)
        
        if fallback_result['success']:
            return {
                'success': True,
                'data': fallback_result['data'],
                'quality': 'degraded',
                'error_handled': error_id,
                'fallback_used': fallback_result['method']
            }
        else:
            return {
                'success': False,
                'error': f"Operation failed: {str(error)}",
                'error_id': error_id,
                'suggested_action': self._get_recovery_suggestion(operation)
            }
    
    def _attempt_fallback(self, operation, context):
        """Attempt fallback strategies for failed operations"""
        fallback_method = self.fallback_strategies.get(operation)
        
        if fallback_method:
            try:
                result = fallback_method(context)
                return {'success': True, 'data': result, 'method': operation}
            except Exception as fallback_error:
                print(f"⚠️ Fallback also failed: {fallback_error}")
        
        return {'success': False, 'method': 'none_available'}
    
    def _fallback_text_extraction(self, context):
        """Fallback for text extraction failures"""
        print("🔄 Using fallback text extraction...")
        # Simplified text extraction as fallback
        return {"text_blocks": [], "method": "fallback_simple"}
    
    def _fallback_image_processing(self, context):
        """Fallback for image processing failures"""
        print("🔄 Using fallback image processing...")
        return {"images": [], "method": "fallback_basic"}
    
    def _fallback_table_parsing(self, context):
        """Fallback for table parsing failures"""
        print("🔄 Using fallback table parsing...")
        return {"tables": [], "method": "fallback_simple"}
    
    def _log_error(self, error_id, operation, error, context):
        """Log error with context"""
        error_entry = {
            'error_id': error_id,
            'timestamp': datetime.now().isoformat(),
            'operation': operation,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'context': str(context)[:200]  # Limit context length
        }
        self.error_log.append(error_entry)
        print(f"📝 Error logged: {error_id} - {operation} - {error}")
    
    def _get_recovery_suggestion(self, operation):
        """Get recovery suggestions based on operation type"""
        suggestions = {
            'text_extraction': 'Try adjusting OCR settings or preprocessing PDF',
            'image_processing': 'Reduce image quality or skip image extraction',
            'table_parsing': 'Use simpler table detection algorithm',
            'default': 'Check PDF quality and try alternative extraction methods'
        }
        return suggestions.get(operation, suggestions['default'])
    
    def generate_error_report(self):
        """Generate error analytics report"""
        if not self.error_log:
            return {"status": "No errors recorded", "health_score": 1.0}
        
        total_errors = len(self.error_log)
        error_types = {}
        
        for error in self.error_log:
            error_type = error['error_type']
            error_types[error_type] = error_types.get(error_type, 0) + 1
        
        return {
            "total_errors": total_errors,
            "error_distribution": error_types,
            "health_score": max(0, 1 - (total_errors / 100)),
            "recent_errors": self.error_log[-5:] if self.error_log else []
        }


# ============================================================
#  VECTOR MATH SYMBOL DETECTION
# ============================================================
def detect_math_drawings(pdf_path: str) -> dict:
    """Detect vector-drawn math shapes with CORRECT coordinates"""
    detected = {}
    doc = fitz.open(pdf_path)
    print(f"\n🔍 Scanning {pdf_path} for vector math symbols...")
    
    for page_num, page in enumerate(doc):
        page_height = page.rect.height  # Get actual page height
        drawings = page.get_drawings()
        math_like = []
        
        for d in drawings:
            bbox = d.get("rect", None)
            if not bbox:
                continue
                
            width = abs(bbox[2] - bbox[0])
            height = abs(bbox[3] - bbox[1])
            aspect = width / max(height, 1)
            
            # FIX: Convert PyMuPDF coordinates to standard PDF (Y=0 at bottom)
            fixed_y0 = page_height - bbox[3]  # Convert Y coordinates
            fixed_y1 = page_height - bbox[1]
            
            # Heuristic for math lines / bars / radicals
            if (height < 3 and width > 10) or (aspect < 0.2 and height > 10):
                math_like.append({
                    "type": "vector_symbol",
                    "symbol_guess": "fraction_bar" if height < 3 else "radical_or_vertical",
                    "bbox": [bbox[0], fixed_y0, bbox[2], fixed_y1],  # FIXED coordinates
                    "dimensions": {"width": width, "height": height},
                    "source": "drawing-stroke",
                    "page": page_num
                })
                
        if math_like:
            detected[page_num] = math_like
            print(f"📐 Page {page_num + 1}: {len(math_like)} math symbols with FIXED coordinates")
            
    doc.close()
    return detected


def merge_math_symbols_into_json(json_path: str, detected_math: dict):
    """Merge detected math symbols directly into the same JSON (no new file)"""
    if not detected_math:
        print("ℹ️ No vector math symbols found.")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for page in data.get("pages", []):
        page_index = page.get("page_number", 1) - 1
        if page_index in detected_math:
            page["vector_symbols"] = detected_math[page_index]

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✅ Math symbols merged into JSON: {json_path}")


# ============================================================
#  TRANSLATION HELPER
# ============================================================
def translate_json_preserve_structure(extracted_json_path: str, translated_json_path: str, target_lang: str = 'te'):
    """
    Translate text content while PRESERVING vector symbols and layout elements
    """
    print(f"\n🌐 Translating JSON while preserving structure...")
    print(f"   Source: {extracted_json_path}")
    print(f"   Target: {translated_json_path}")
    print(f"   Language: {target_lang}")
    
    with open(extracted_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Count elements before translation for verification
    original_vector_count = sum(len(page.get('vector_symbols', [])) for page in data['pages'])
    original_layout_count = sum(len(page.get('layout_elements', [])) for page in data['pages'])
    
    print(f"   Original vector symbols: {original_vector_count}")
    print(f"   Original layout elements: {original_layout_count}")
    
    # Translate only the text content that should be translated
    translated_blocks = 0
    for page in data['pages']:
        for text_block in page['text_content']:
            if text_block.get('translation_ready', True) and text_block.get('content_type') != 'mathematical':
                original_text = text_block['content']
                
                # ⭐⭐ SIMULATE TRANSLATION - REPLACE WITH REAL TRANSLATION API ⭐⭐
                if target_lang == 'te':  # Telugu
                    # This is a placeholder - replace with actual translation
                    translated_text = f"[Telugu: {original_text[:50]}...]"
                else:
                    translated_text = original_text
                
                text_block['translated_content'] = translated_text
                translated_blocks += 1
    
    print(f"   Translated text blocks: {translated_blocks}")
    
    # ⭐⭐⭐ CRITICAL: Verify vector symbols and layout elements are preserved ⭐⭐⭐
    final_vector_count = sum(len(page.get('vector_symbols', [])) for page in data['pages'])
    final_layout_count = sum(len(page.get('layout_elements', [])) for page in data['pages'])
    
    print(f"   Final vector symbols: {final_vector_count}")
    print(f"   Final layout elements: {final_layout_count}")
    
    if final_vector_count == original_vector_count and final_layout_count == original_layout_count:
        print("✅ SUCCESS: All vector symbols and layout elements preserved!")
    else:
        print("❌ WARNING: Some elements may have been lost during translation!")
    
    # Save translated JSON
    with open(translated_json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Translated JSON saved: {translated_json_path}")
    return data


# ============================================================
#  JSON VERIFICATION FUNCTION
# ============================================================
def verify_json_structure(json_path: str, expected_vector_count: int = 82, expected_layout_count: int = 77):
    """Verify that JSON contains all necessary elements for PDF generation"""
    print(f"\n🔍 Verifying JSON structure: {json_path}")
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    total_vector_symbols = 0
    total_layout_lines = 0
    total_text_blocks = 0
    total_images = 0
    
    for page_num, page in enumerate(data.get('pages', [])):
        vector_symbols = page.get('vector_symbols', [])
        layout_elements = page.get('layout_elements', [])
        text_content = page.get('text_content', [])
        images = page.get('images', [])
        
        total_vector_symbols += len(vector_symbols)
        total_layout_lines += len([e for e in layout_elements if e.get('type') == 'line'])
        total_text_blocks += len(text_content)
        total_images += len(images)
    
    print(f"📊 JSON Content Summary:")
    print(f"   • Vector symbols: {total_vector_symbols} (expected: {expected_vector_count})")
    print(f"   • Layout lines: {total_layout_lines} (expected: {expected_layout_count})")
    print(f"   • Text blocks: {total_text_blocks}")
    print(f"   • Images: {total_images}")
    
    # Check if ready for PDF generation
    has_vector_symbols = total_vector_symbols > 0
    has_layout_elements = total_layout_lines > 0
    has_text_content = total_text_blocks > 0
    
    if has_vector_symbols and has_layout_elements and has_text_content:
        print("✅ READY for PDF generation - All elements present!")
        return True
    else:
        print("❌ NOT READY for PDF generation - Missing elements!")
        if not has_vector_symbols:
            print("   - Missing vector symbols")
        if not has_layout_elements:
            print("   - Missing layout elements") 
        if not has_text_content:
            print("   - Missing text content")
        return False


# ============================================================
#  MAIN PDF → JSON CONVERTER CLASS - ENHANCED VERSION
# ============================================================
class PDFToJSONConverter:
    def __init__(self):
        self.supported_formats = ['.pdf']
        self.bar_chart_detector = EnhancedBarChartDetector()
        self.error_handler = GracefulDegradation()

    def _is_translatable_content(self, text: str) -> bool:
        """Determine if text block should be translated - ENHANCED VERSION"""
        if not text or len(text.strip()) < 2:
            return False
        
        # Don't translate Telugu text
        telugu_chars = re.findall(r'[\u0C00-\u0C7F]', text)
        if telugu_chars:
            return False
        
        # Don't translate mathematical expressions
        math_indicators = ['√', '×', '÷', '²', '³', '=', '%', 'square root']
        if any(symbol in text for symbol in math_indicators):
            return False
        
        # Don't translate pure numbers/options
        if re.match(r'^[1-5]\)\s*\d+$', text.strip()):
            return False
        
        # Don't translate number sequences
        if re.match(r'^\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\?$', text.strip()):
            return False
        
        # Don't translate acronyms
        acronyms = ['SBI', 'LIC', 'NIACL', 'MTS', 'CGL', 'CHSL', 'CCE', 'RRB', 'IBPS']
        if all(word in acronyms or word.isdigit() for word in text.upper().split()):
            return False
        
        return True

    def _classify_content_type(self, text: str) -> str:
        """Enhanced content classification"""
        return improved_content_classification(text)

    # ============================================================
    #  ENHANCED TEXT EXTRACTION
    # ============================================================
    def _extract_text_enhanced(self, page, coord_converter) -> List[Dict[str, Any]]:
        """Enhanced text extraction with consistent coordinates"""
        text_blocks = []
        try:
            words = page.extract_words(
                extra_attrs=["fontname", "size"], 
                x_tolerance=1.5,
                y_tolerance=2,
                keep_blank_chars=False,
                use_text_flow=True
            )
            
            current_block, current_y, y_tol = [], None, 5
            for w in sorted(words, key=lambda w: (w['top'], w['x0'])):
                w_text = enhanced_clean_extracted_text(w['text'].strip())
                if not w_text:
                    continue
                    
                if current_y is None or abs(w['top'] - current_y) > y_tol:
                    if current_block:
                        text_blocks.append(self._create_text_block_enhanced(current_block, coord_converter))
                    current_block, current_y = [w], w['top']
                else:
                    current_block.append(w)
                    
            if current_block:
                text_blocks.append(self._create_text_block_enhanced(current_block, coord_converter))
                
        except Exception as e:
            print(f"    ⚠️ Enhanced text extraction failed: {e}")
            # Fallback to basic extraction
            try:
                text = page.extract_text()
                if text:
                    cleaned_text = enhanced_clean_extracted_text(text)
                    text_blocks.append({
                        "type": "text_block", 
                        "content": cleaned_text,
                        "position": {"x0": 0, "y0": 0, "x1": float(page.width), "y1": float(page.height)},
                        "font": {"name": "", "size": 0},
                        "word_count": len(cleaned_text.split()),
                        "translation_ready": self._is_translatable_content(cleaned_text),
                        "content_type": self._classify_content_type(cleaned_text),
                        "extraction_method": "fallback_basic"
                    })
            except:
                pass
                
        return text_blocks

    def _create_text_block_enhanced(self, words: List[Dict[str, Any]], coord_converter) -> Dict[str, Any]:
        """Create text block with consistent coordinates"""
        if not words:
            return {}
            
        x0, top = min(w['x0'] for w in words), min(w['top'] for w in words)
        x1, bottom = max(w['x1'] for w in words), max(w['bottom'] for w in words)
        text_content = ' '.join(enhanced_clean_extracted_text(w['text']) for w in words)
        fonts = [w.get('fontname', '') for w in words if w.get('fontname')]
        font_size = words[0].get('size', 0) if words else 0
        
        # Use coordinate converter for consistent coordinates
        fixed_top = coord_converter.pdfplumber_to_standard(bottom)
        fixed_bottom = coord_converter.pdfplumber_to_standard(top)
        
        content_type = self._classify_content_type(text_content)
        should_translate = self._is_translatable_content(text_content)
        
        return {
            "type": "text_block",
            "content": text_content,
            "position": {
                "x0": float(x0), "y0": float(fixed_top),
                "x1": float(x1), "y1": float(fixed_bottom),
                "width": float(x1 - x0), "height": float(bottom - top)
            },
            "font": {
                "name": max(set(fonts), key=fonts.count) if fonts else "", 
                "size": float(font_size),
                "bold": any('bold' in f.lower() for f in fonts),
                "italic": any('italic' in f.lower() for f in fonts)
            },
            "word_count": len(words),
            "translation_ready": should_translate,
            "content_type": content_type,
            "extraction_method": "enhanced"
        }

    # ============================================================
    #  ENHANCED TABLE EXTRACTION
    # ============================================================
    def _extract_tables_enhanced(self, page, coord_converter) -> List[Dict[str, Any]]:
        """Extract tables with better structure detection"""
        tables = []
        try:
            table_objects = page.find_tables()
            extracted_tables = page.extract_tables()
            
            for i, (table_obj, table_data) in enumerate(zip(table_objects, extracted_tables)):
                if table_data and any(any(cell for cell in row) for row in table_data):
                    # Convert coordinates
                    fixed_y0 = coord_converter.pdfplumber_to_standard(table_obj.bbox[3])
                    fixed_y1 = coord_converter.pdfplumber_to_standard(table_obj.bbox[1])
                    
                    has_header = self._detect_table_header(table_data)
                    
                    tables.append({
                        "table_number": i + 1,
                        "position": {
                            "x0": float(table_obj.bbox[0]), "y0": float(fixed_y0),
                            "x1": float(table_obj.bbox[2]), "y1": float(fixed_y1)
                        },
                        "data": table_data,
                        "structure": {
                            "rows": len(table_data),
                            "columns": len(table_data[0]) if table_data else 0,
                            "has_header": has_header,
                            "header_row": 0 if has_header else None
                        },
                        "translation_ready": not has_header,
                        "extraction_method": "enhanced"
                    })
                    
        except Exception as e:
            print(f"    ⚠️ Enhanced table extraction failed: {e}")
            
        return tables

    def _detect_table_header(self, table_data):
        """Detect if table has a header row"""
        if not table_data or len(table_data) < 2:
            return False
        
        first_row = table_data[0]
        second_row = table_data[1] if len(table_data) > 1 else []
        
        # Simple heuristic: header often has text while data has numbers
        first_row_has_text = any(cell and not cell.replace('.', '').isdigit() for cell in first_row if cell)
        second_row_has_numbers = any(cell and cell.replace('.', '').isdigit() for cell in second_row if cell)
        
        return first_row_has_text and second_row_has_numbers

    # ============================================================
    #  ENHANCED IMAGE EXTRACTION WITH ROBUST ERROR HANDLING
    # ============================================================
    def _extract_images_enhanced(self, page, include_images, image_handling, 
                                page_num, fitz_doc, coord_converter):
        """Enhanced image extraction with real coordinates and robust error handling"""
        images = []
        if not include_images or not fitz_doc:
            return images
        
        try:
            pymupdf_page = fitz_doc[page_num]
            page_height = pymupdf_page.rect.height
            image_list = pymupdf_page.get_images(full=True)
            
            print(f"    📷 Enhanced image extraction: found {len(image_list)} images")
            
            successful_extractions = 0
            failed_extractions = 0
            
            for idx, img in enumerate(image_list):
                try:
                    xref = img[0]
                    
                    # Enhanced error handling for image extraction
                    try:
                        base_image = fitz_doc.extract_image(xref)
                        if not base_image:
                            print(f"    ⚠️ Image {idx + 1}: No image data returned")
                            failed_extractions += 1
                            continue
                            
                        img_bytes = base_image.get("image")
                        if not img_bytes:
                            print(f"    ⚠️ Image {idx + 1}: Empty image bytes")
                            failed_extractions += 1
                            continue
                            
                    except Exception as extract_error:
                        print(f"    ⚠️ Image {idx + 1}: Extraction failed - {extract_error}")
                        failed_extractions += 1
                        continue
                    
                    # Get image properties with fallbacks
                    img_ext = base_image.get("ext", "png")
                    width = base_image.get("width", 0)
                    height = base_image.get("height", 0)
                    
                    if width == 0 or height == 0:
                        print(f"    ⚠️ Image {idx + 1}: Invalid dimensions {width}x{height}")
                        failed_extractions += 1
                        continue
                    
                    # Get image position with enhanced error handling
                    try:
                        img_rect = pymupdf_page.get_image_bbox(img)
                        x0, y0, x1, y1 = img_rect
                        
                        # Validate coordinates
                        if x1 <= x0 or y1 <= y0:
                            print(f"    ⚠️ Image {idx + 1}: Invalid bounding box {x0},{y0},{x1},{y1}")
                            # Use fallback position
                            x0, y0 = 50 + (idx * 10), 50 + (idx * 10)
                            x1, y1 = x0 + width, y0 + height
                            
                    except Exception as coord_error:
                        print(f"    ⚠️ Image {idx + 1}: Coordinate extraction failed - {coord_error}")
                        # Use fallback position
                        x0, y0 = 50 + (idx * 10), 50 + (idx * 10)
                        x1, y1 = x0 + width, y0 + height
                    
                    # Convert coordinates to standard system
                    fixed_y0 = coord_converter.pymupdf_to_standard(y0, height)
                    fixed_y1 = coord_converter.pymupdf_to_standard(y1, height)
                    
                    image_info = {
                        "image_number": idx + 1,
                        "position": {
                            "x0": float(x0), 
                            "y0": float(fixed_y0),
                            "x1": float(x1),
                            "y1": float(fixed_y1),
                            "width": float(width), 
                            "height": float(height)
                        },
                        "dimensions": {"width": float(width), "height": float(height)},
                        "extraction_method": "enhanced_pymupdf",
                        "page": page_num,
                        "image_format": img_ext,
                        "image_size_bytes": len(img_bytes)
                    }
                    
                    # Enhanced bar chart detection
                    try:
                        page_text_blocks = self._extract_text_enhanced(page, coord_converter)
                        chart_detection = self.bar_chart_detector.detect_bar_chart_enhanced(
                            image_info, page_text_blocks, page_num
                        )
                        
                        axis_data = {}
                        if chart_detection['is_bar_chart_candidate']:
                            axis_data = extract_axis_labels_from_context(page_text_blocks, image_info['position'])
                        
                        image_info.update({
                            "is_bar_chart_candidate": chart_detection['is_bar_chart_candidate'],
                            "bar_chart_confidence": chart_detection['confidence'],
                            "chart_type": chart_detection['chart_type'],
                            "detection_features": chart_detection['detection_features'],
                            "extracted_axis_data": axis_data,
                            "description": f"Image {idx+1} ({width}x{height})" + 
                                          (" [BAR CHART]" if chart_detection['is_bar_chart_candidate'] else "")
                        })
                    except Exception as chart_error:
                        print(f"    ⚠️ Chart detection failed for image {idx + 1}: {chart_error}")
                        image_info.update({
                            "is_bar_chart_candidate": False,
                            "bar_chart_confidence": 0.0,
                            "chart_type": "unknown",
                            "detection_features": ["chart_detection_failed"]
                        })
                    
                    # Handle image data based on user preference
                    try:
                        if image_handling == "base64":
                            b64 = base64.b64encode(img_bytes).decode("utf-8")
                            image_info["data"] = b64
                            image_info["image_size_kb"] = len(img_bytes) / 1024
                        elif image_handling == "external":
                            img_filename = f"page_{page_num+1}_img_{idx+1}.{img_ext}"
                            img_path = Path("extracted_images") / img_filename
                            img_path.parent.mkdir(exist_ok=True)
                            with open(img_path, "wb") as f:
                                f.write(img_bytes)
                            image_info["external_path"] = str(img_path)
                            image_info["image_size_kb"] = len(img_bytes) / 1024
                        else:  # metadata only
                            image_info["image_size_kb"] = len(img_bytes) / 1024
                            
                    except Exception as data_error:
                        print(f"    ⚠️ Image data handling failed for image {idx + 1}: {data_error}")
                        # Continue with metadata only
                        image_info["image_size_kb"] = len(img_bytes) / 1024
                        image_info["data_handling_error"] = str(data_error)
                    
                    images.append(image_info)
                    successful_extractions += 1
                    
                    if image_info.get('is_bar_chart_candidate', False):
                        print(f"    ✅ ENHANCED BAR CHART: Image {idx + 1} - Confidence: {image_info['bar_chart_confidence']:.2f}")
                    else:
                        print(f"    ✅ Extracted image {idx + 1} ({width}x{height}) at position {x0:.1f}, {y0:.1f}")
                            
                except Exception as e:
                    print(f"    ⚠️ Enhanced image extraction failed for image {idx + 1}: {e}")
                    failed_extractions += 1
                    
            # Summary for the page
            if successful_extractions > 0:
                print(f"    📊 Image extraction summary: {successful_extractions} successful, {failed_extractions} failed")
            else:
                print(f"    ❌ No images successfully extracted from page {page_num + 1}")
                # Try fallback extraction
                fallback_images = self._extract_images_fallback(page, include_images, image_handling, page_num, fitz_doc, coord_converter)
                images.extend(fallback_images)
                        
        except Exception as e:
            print(f"    ❌ Enhanced image extraction failed on page {page_num + 1}: {e}")
            
        return images

    def _extract_images_fallback(self, page, include_images, image_handling, 
                               page_num, fitz_doc, coord_converter):
        """Fallback image extraction using alternative methods"""
        images = []
        if not include_images or not fitz_doc:
            return images
        
        try:
            pymupdf_page = fitz_doc[page_num]
            
            # Alternative approach: extract images as PDF pixmaps
            pix = pymupdf_page.get_pixmap(matrix=fitz.Matrix(1, 1))
            if pix:
                image_info = {
                    "image_number": 1,
                    "position": {
                        "x0": 0, 
                        "y0": 0,
                        "width": float(pix.width), 
                        "height": float(pix.height)
                    },
                    "dimensions": {"width": float(pix.width), "height": float(pix.height)},
                    "extraction_method": "fallback_pixmap",
                    "page": page_num,
                    "description": "Full page raster extraction (fallback)"
                }
                
                if image_handling == "base64":
                    img_bytes = pix.tobytes("png")
                    b64 = base64.b64encode(img_bytes).decode("utf-8")
                    image_info["data"] = b64
                    image_info["image_size_kb"] = len(img_bytes) / 1024
                    
                images.append(image_info)
                print(f"    ✅ Fallback: Extracted page as image ({pix.width}x{pix.height})")
                
        except Exception as e:
            print(f"    ⚠️ Fallback image extraction failed: {e}")
            
        return images

    # ============================================================
    #  ENHANCED LAYOUT EXTRACTION
    # ============================================================
    def _extract_layout_enhanced(self, page, coord_converter):
        """Extract layout elements with consistent coordinates"""
        elements = []
        
        try:
            # Extract lines
            for line in page.lines:
                fixed_y0 = coord_converter.pdfplumber_to_standard(line['top'])
                fixed_y1 = coord_converter.pdfplumber_to_standard(line['bottom'])
                
                elements.append({
                    "type": "line",
                    "position": {
                        "x0": float(line['x0']), "y0": float(fixed_y0),
                        "x1": float(line['x1']), "y1": float(fixed_y1)
                    },
                    "width": float(line['width']), 
                    "height": float(line['height']),
                    "extraction_method": "enhanced"
                })
                
            # Extract curves (if any)
            for curve in page.curves:
                elements.append({
                    "type": "curve",
                    "points": curve.get('points', []),
                    "extraction_method": "enhanced"
                })
                
        except Exception as e:
            print(f"    ⚠️ Enhanced layout extraction failed: {e}")
            
        return elements

    # ============================================================
    #  ENHANCED VECTOR SYMBOLS EXTRACTION
    # ============================================================
    def _extract_vector_symbols_enhanced(self, fitz_doc, page_num, coord_converter):
        """Extract vector symbols with consistent coordinates"""
        symbols = []
        
        try:
            page = fitz_doc[page_num]
            page_height = page.rect.height
            drawings = page.get_drawings()
            
            for d in drawings:
                bbox = d.get("rect", None)
                if not bbox:
                    continue
                    
                width = abs(bbox[2] - bbox[0])
                height = abs(bbox[3] - bbox[1])
                aspect = width / max(height, 1)
                
                # Convert coordinates
                fixed_y0 = coord_converter.pymupdf_to_standard(bbox[3], 0)
                fixed_y1 = coord_converter.pymupdf_to_standard(bbox[1], 0)
                
                # Enhanced symbol detection
                symbol_type = "unknown"
                if height < 3 and width > 10:
                    symbol_type = "fraction_bar"
                elif aspect < 0.2 and height > 10:
                    symbol_type = "vertical_line"
                elif 0.8 <= aspect <= 1.2 and width > 5 and height > 5:
                    symbol_type = "square_or_circle"
                
                symbols.append({
                    "type": "vector_symbol",
                    "symbol_guess": symbol_type,
                    "bbox": [bbox[0], fixed_y0, bbox[2], fixed_y1],
                    "dimensions": {"width": width, "height": height},
                    "source": "drawing-stroke",
                    "page": page_num,
                    "extraction_method": "enhanced"
                })
                
        except Exception as e:
            print(f"    ⚠️ Enhanced vector symbol extraction failed: {e}")
            
        return symbols

    # ============================================================
    #  MAIN ENHANCED CONVERSION METHOD
    # ============================================================
    def convert_pdf_to_json_enhanced(self, pdf_path: str, output_path: str = None,
                                    include_images: bool = False,
                                    image_handling: str = "metadata") -> Dict[str, Any]:
        """Enhanced PDF to JSON conversion with consistent coordinates"""
        
        if not Path(pdf_path).exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        print(f"🚀 ENHANCED PDF Conversion: {Path(pdf_path).name}")
        print(f"   Image handling: {image_handling}")
        print(f"   Include images: {include_images}")

        # Single PDF opening for all operations
        with pdfplumber.open(pdf_path) as pdf:
            fitz_doc = fitz.open(pdf_path)
            
            pdf_data = {
                "metadata": self._extract_enhanced_metadata(pdf_path, pdf),
                "pages": [],
                "extraction_settings": {
                    "coordinate_system": "standard_pdf_y0_at_bottom",
                    "units": "points", 
                    "text_cleaning": "enhanced_ocr_correction",
                    "image_handling": image_handling,
                    "include_images": include_images,
                    "timestamp": datetime.now().isoformat()
                }
            }
            
            for page_num, page in enumerate(pdf.pages):
                print(f"  📄 Processing page {page_num + 1}/{len(pdf.pages)}...")
                
                # Unified coordinate converter for this page
                coord_converter = CoordinateConverter(page.height)
                
                page_data = {
                    "page_number": page_num + 1,
                    "dimensions": {
                        "width": float(page.width), 
                        "height": float(page.height),
                        "rotation": page.rotation or 0
                    },
                    "text_content": self._extract_text_enhanced(page, coord_converter),
                    "tables": self._extract_tables_enhanced(page, coord_converter),
                    "images": self._extract_images_enhanced(page, include_images, image_handling, page_num, fitz_doc, coord_converter),
                    "layout_elements": self._extract_layout_enhanced(page, coord_converter),
                    "vector_symbols": self._extract_vector_symbols_enhanced(fitz_doc, page_num, coord_converter)
                }
                
                pdf_data["pages"].append(page_data)
            
            fitz_doc.close()
        
        # Apply enhanced text processing
        pdf_data = self._apply_enhanced_text_processing(pdf_data)
        
        # Comprehensive validation
        validation = self._comprehensive_validation(pdf_data)
        pdf_data["validation"] = validation
        
        # Save with better structure
        if output_path:
            self._save_enhanced_json(pdf_data, output_path)
        
        return pdf_data

    def _extract_enhanced_metadata(self, pdf_path, pdf):
        """Extract enhanced metadata"""
        path = Path(pdf_path)
        return {
            "file_name": path.name,
            "file_size": path.stat().st_size,
            "file_format": "PDF",
            "total_pages": len(pdf.pages),
            "extraction_date": datetime.now().isoformat(),
            "pdf_version": getattr(pdf, 'metadata', {}).get('PDF', 'Unknown')
        }

    def _apply_enhanced_text_processing(self, pdf_data):
        """Apply enhanced text cleaning and merging"""
        enhanced_data = pdf_data.copy()
        
        for page in enhanced_data["pages"]:
            text_content = page.get("text_content", [])
            
            if text_content:
                # Clean and classify each block
                cleaned_blocks = []
                for block in text_content:
                    cleaned_block = block.copy()
                    original_content = block.get("content", "")
                    cleaned_content = enhanced_clean_extracted_text(original_content)
                    cleaned_block["content"] = cleaned_content
                    
                    improved_type = improved_content_classification(cleaned_content)
                    cleaned_block["content_type"] = improved_type
                    
                    # Update translation readiness
                    if improved_type in ["telugu_text", "mathematical", "data_label", "exam_reference"]:
                        cleaned_block["translation_ready"] = False
                    
                    cleaned_blocks.append(cleaned_block)
                
                # Merge fragmented blocks
                merged_blocks = merge_fragmented_text_blocks(cleaned_blocks)
                page["text_content"] = merged_blocks
                
                # Log improvements
                original_count = len(text_content)
                merged_count = len(merged_blocks)
                if merged_count < original_count:
                    print(f"   ✅ Merged {original_count - merged_count} fragmented text blocks")
        
        return enhanced_data

    def _comprehensive_validation(self, pdf_data):
        """Detailed validation of extracted content"""
        validation = {
            "summary": {},
            "page_breakdown": [],
            "issues": [],
            "recommendations": []
        }
        
        total_text_blocks = 0
        total_tables = 0
        total_images = 0
        total_vector_symbols = 0
        
        for page in pdf_data["pages"]:
            page_stats = {
                "page": page["page_number"],
                "text_blocks": len(page.get("text_content", [])),
                "tables": len(page.get("tables", [])),
                "images": len(page.get("images", [])),
                "vector_symbols": len(page.get("vector_symbols", [])),
                "quality_indicators": self._analyze_page_quality(page)
            }
            
            validation["page_breakdown"].append(page_stats)
            total_text_blocks += page_stats["text_blocks"]
            total_tables += page_stats["tables"]
            total_images += page_stats["images"]
            total_vector_symbols += page_stats["vector_symbols"]
        
        # Overall summary
        validation["summary"] = {
            "total_pages": len(pdf_data["pages"]),
            "total_text_blocks": total_text_blocks,
            "total_tables": total_tables,
            "total_images": total_images,
            "total_vector_symbols": total_vector_symbols,
            "extraction_completeness": self._calculate_completeness_score(pdf_data)
        }
        
        # Generate recommendations
        if total_text_blocks == 0:
            validation["recommendations"].append("Enable OCR for better text extraction")
        if total_vector_symbols > 0:
            validation["recommendations"].append("Vector symbols detected - consider mathematical content processing")
        if total_tables == 0:
            validation["recommendations"].append("No tables detected - check table extraction settings")
        
        return validation

    def _analyze_page_quality(self, page):
        """Analyze quality indicators for a page"""
        text_blocks = page.get("text_content", [])
        if not text_blocks:
            return {"score": 0, "issues": ["No text content"]}
        
        issues = []
        valid_blocks = 0
        
        for block in text_blocks:
            content = block.get("content", "")
            if len(content.strip()) > 1:  # Non-empty content
                valid_blocks += 1
            else:
                issues.append("Empty text block")
        
        score = valid_blocks / len(text_blocks) if text_blocks else 0
        return {"score": score, "issues": issues, "valid_blocks": valid_blocks}

    def _calculate_completeness_score(self, pdf_data):
        """Calculate overall extraction completeness score"""
        total_pages = len(pdf_data["pages"])
        if total_pages == 0:
            return 0
        
        page_scores = []
        for page in pdf_data["pages"]:
            quality = self._analyze_page_quality(page)
            page_scores.append(quality["score"])
        
        return sum(page_scores) / len(page_scores) if page_scores else 0

    def _save_enhanced_json(self, data: Dict[str, Any], output_path: str):
        """Save JSON with better formatting and error handling"""
        try:
            # Create directory if it doesn't exist
            output_dir = Path(output_path).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            
            file_size = Path(output_path).stat().st_size
            print(f"✅ Enhanced JSON saved: {output_path} ({file_size / 1024:.1f} KB)")
            
        except Exception as e:
            print(f"❌ Failed to save enhanced JSON: {e}")
            raise


# ============================================================
#  MAIN EXECUTION
# ============================================================
def main():
    converter = PDFToJSONConverter()
    pdf_path = "SBI Clerk Prelims.pdf"
    
    print("🎯 ENHANCED PDF Extraction Modes:")
    print("1. Text + Tables only (fast)")
    print("2. Text + Tables + Image metadata (recommended)")
    print("3. Text + Tables + External images (best for analysis)")
    print("4. Everything in JSON (large files)")
    
    choice = input("Choose mode (1-4, default 2): ").strip() or "2"
    
    modes = {
        "1": {"include_images": False, "image_handling": "metadata"},
        "2": {"include_images": True, "image_handling": "metadata"},
        "3": {"include_images": True, "image_handling": "external"},
        "4": {"include_images": True, "image_handling": "base64"}
    }
    
    config = modes.get(choice, modes["2"])
    output_json = "sbi_extracted.json"
    
    # Run enhanced extraction
    try:
        result = converter.convert_pdf_to_json_enhanced(
            pdf_path, 
            output_json, 
            **config
        )
        
        print(f"\n🎉 Enhanced extraction completed!")
        print(f"📊 Validation Score: {result['validation']['summary']['extraction_completeness']:.2f}")
        print(f"📄 Total pages: {result['validation']['summary']['total_pages']}")
        print(f"📝 Text blocks: {result['validation']['summary']['total_text_blocks']}")
        print(f"📊 Tables: {result['validation']['summary']['total_tables']}")
        print(f"🖼️ Images: {result['validation']['summary']['total_images']}")
        
    except Exception as e:
        print(f"❌ Enhanced extraction failed: {e}")


if __name__ == "__main__":
    main()