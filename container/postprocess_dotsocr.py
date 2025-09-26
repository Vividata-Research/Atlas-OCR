#!/usr/bin/env python3
"""
DotsOCR Postprocessing Script

This script processes DotsOCR output by:
1. Concatenating all markdown files in chronological order
2. Extracting base64 images and saving them as PNG files in an assets folder
3. Replacing base64 image references with simple image references (image1.png, image2.png, etc.)
4. Creating a final consolidated markdown file

Usage:
    python3 postprocess_dotsocr.py <input_directory> [output_filename]
    
Example:
    python3 postprocess_dotsocr.py output/sample
    python3 postprocess_dotsocr.py output/sample final_document.md
"""

import os
import sys
import re
import base64
import glob
from pathlib import Path

def extract_page_number(filename):
    """Extract page number from filename like 'sample_page_5.md'"""
    match = re.search(r'_page_(\d+)\.md$', filename)
    return int(match.group(1)) if match else 0

def extract_base64_images(content, assets_dir, image_counter_start=1):
    """
    Extract base64 images from markdown content and replace with image references
    
    Returns:
        tuple: (modified_content, final_image_counter)
    """
    # Pattern to match base64 images in markdown
    pattern = r'!\[([^\]]*)\]\(data:image/([^;]+);base64,([^)]+)\)'
    
    image_counter = image_counter_start
    
    def replace_image(match):
        nonlocal image_counter
        
        alt_text = match.group(1)
        image_format = match.group(2)
        base64_data = match.group(3)
        
        # Decode base64 data
        try:
            image_data = base64.b64decode(base64_data)
        except Exception as e:
            print(f"Warning: Failed to decode base64 image {image_counter}: {e}")
            return match.group(0)  # Return original if decode fails
        
        # Create filename
        image_filename = f"image{image_counter}.{image_format}"
        image_path = os.path.join(assets_dir, image_filename)
        
        # Save image
        try:
            with open(image_path, 'wb') as f:
                f.write(image_data)
            print(f"Extracted image: {image_filename}")
        except Exception as e:
            print(f"Warning: Failed to save image {image_filename}: {e}")
            return match.group(0)  # Return original if save fails
        
        # Replace with image reference
        replacement = f"![{alt_text}](assets/{image_filename})"
        image_counter += 1
        
        return replacement
    
    # Replace all base64 images
    modified_content = re.sub(pattern, replace_image, content, flags=re.DOTALL)
    
    return modified_content, image_counter

def process_dotsocr_output(input_dir, output_filename=None):
    """
    Process DotsOCR output directory
    
    Args:
        input_dir: Directory containing DotsOCR output files
        output_filename: Name for final markdown file (optional)
    """
    input_path = Path(input_dir)
    
    if not input_path.exists():
        print(f"Error: Input directory '{input_dir}' does not exist")
        return False
    
    # Find all markdown files (prefer regular .md over _nohf.md)
    md_files = []
    
    # Get all .md files
    all_md_files = list(input_path.glob("*.md"))
    
    # Group by page number and prefer non-nohf versions
    page_files = {}
    for md_file in all_md_files:
        page_num = extract_page_number(md_file.name)
        if page_num not in page_files:
            page_files[page_num] = []
        page_files[page_num].append(md_file)
    
    # Select best file for each page (prefer non-nohf)
    for page_num in sorted(page_files.keys()):
        files = page_files[page_num]
        # Prefer files without _nohf
        regular_files = [f for f in files if '_nohf' not in f.name]
        if regular_files:
            md_files.append(regular_files[0])
        else:
            md_files.append(files[0])
    
    if not md_files:
        print(f"Error: No markdown files found in '{input_dir}'")
        return False
    
    print(f"Found {len(md_files)} markdown files to process")
    
    # Create output_consolidated directory
    output_consolidated_dir = Path("output_consolidated")
    output_consolidated_dir.mkdir(exist_ok=True)
    
    # Create subdirectory for this specific document
    document_name = input_path.name
    document_output_dir = output_consolidated_dir / document_name
    document_output_dir.mkdir(exist_ok=True)
    
    # Create assets directory within the document output directory
    assets_dir = document_output_dir / "assets"
    assets_dir.mkdir(exist_ok=True)
    
    # Process files
    consolidated_content = []
    image_counter = 1
    
    for md_file in md_files:
        print(f"Processing: {md_file.name}")
        
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"Warning: Failed to read {md_file.name}: {e}")
            continue
        
        # Extract images and replace references
        modified_content, image_counter = extract_base64_images(
            content, assets_dir, image_counter
        )
        
        # Add page separator and content
        if consolidated_content:
            consolidated_content.append("\n\n---\n\n")  # Page separator
        
        consolidated_content.append(f"<!-- Page {extract_page_number(md_file.name)} -->\n")
        consolidated_content.append(modified_content)
    
    # Determine output filename
    if output_filename is None:
        output_filename = f"{input_path.name}_consolidated.md"
    
    output_path = document_output_dir / output_filename
    
    # Write consolidated file
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(''.join(consolidated_content))
        
        print(f"\n✅ Successfully created consolidated markdown: {output_path}")
        print(f"📁 Document output directory: {document_output_dir}")
        print(f"📁 Assets directory: {assets_dir}")
        print(f"🖼️  Extracted {image_counter - 1} images")
        
        return True
        
    except Exception as e:
        print(f"Error: Failed to write output file: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    input_dir = sys.argv[1]
    output_filename = sys.argv[2] if len(sys.argv) > 2 else None
    
    success = process_dotsocr_output(input_dir, output_filename)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
