import os
import sys

try:
    import pathspec
    HAS_PATHSPEC = True
except ImportError:
    HAS_PATHSPEC = False
    print("⚠️ Warning: 'pathspec' library not found. Installing it is recommended.")

def load_ignore_patterns(root_dir):
    """الگوهای نادیده گرفتن را می‌خواند."""
    
    # اسم همین اسکریپت که الان دارد اجرا می‌شود
    current_script_name = os.path.basename(__file__)
    
    patterns = [
        '.git/', '.idea/', '__pycache__/', '*.pyc', '*.pyo', 
        'node_modules/', 'venv/', '.env', '.DS_Store',
        'project_context.txt', 
        current_script_name  # 🔥 فیکس: نام خود اسکریپت را داینامیک اضافه کردیم
    ]

    ignore_files = ['.gitignore', '.dockerignore']
    
    for ignore_file in ignore_files:
        path = os.path.join(root_dir, ignore_file)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        patterns.append(line)
    return patterns

# ... بقیه توابع (is_ignored, generate_tree) بدون تغییر می‌مانند ...

def is_ignored(path, root_dir, spec):
    rel_path = os.path.relpath(path, root_dir)
    if HAS_PATHSPEC and spec:
        return spec.match_file(rel_path)
    for pattern in spec:
        clean_pattern = pattern.replace('/', '').replace('*', '')
        if clean_pattern in rel_path:
            return True
    return False

def generate_tree(root_dir, spec):
    tree_output = []
    
    def walk_tree(directory, prefix=""):
        try:
            items = os.listdir(directory)
        except PermissionError:
            return # اسکیپ کردن فولدرهای سیستمی قفل شده
            
        items = sorted(items, key=lambda x: (os.path.isdir(os.path.join(directory, x)), x))
        
        filtered_items = []
        for item in items:
            full_path = os.path.join(directory, item)
            if not is_ignored(full_path, root_dir, spec):
                filtered_items.append(item)
        
        for index, item in enumerate(filtered_items):
            path = os.path.join(directory, item)
            is_last = index == len(filtered_items) - 1
            connector = "└── " if is_last else "├── "
            
            if os.path.isdir(path):
                tree_output.append(f"{prefix}{connector}📂 {item}/")
                extension = "    " if is_last else "│   "
                walk_tree(path, prefix + extension)
            else:
                tree_output.append(f"{prefix}{connector}📄 {item}")

    walk_tree(root_dir)
    return "📦 PROJECT STRUCTURE:\n.\n" + "\n".join(tree_output)

def main():
    root_dir = os.getcwd()
    output_file = "project_context.txt"
    
    print(f"🚀 Scanning directory: {root_dir}")
    patterns = load_ignore_patterns(root_dir)
    
    spec = None
    if HAS_PATHSPEC:
        spec = pathspec.PathSpec.from_lines('gitwildmatch', patterns)
    else:
        spec = patterns

    with open(output_file, 'w', encoding='utf-8') as out:
        print("🌳 Generating tree structure...")
        tree = generate_tree(root_dir, spec)
        out.write(tree)
        out.write("\n\n" + "="*50 + "\n\n")
        
        print("📝 Reading files...")
        file_count = 0
        
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if not is_ignored(os.path.join(dirpath, d), root_dir, spec)]
            
            for f in filenames:
                file_path = os.path.join(dirpath, f)
                if is_ignored(file_path, root_dir, spec):
                    continue
                
                rel_path = os.path.relpath(file_path, root_dir)
                
                try:
                    with open(file_path, 'r', encoding='utf-8') as content_file:
                        content = content_file.read()
                        out.write(f"START OF FILE: {rel_path}\n")
                        out.write("-" * 30 + "\n")
                        out.write(content)
                        out.write("\n" + "-" * 30 + "\n")
                        out.write(f"END OF FILE: {rel_path}\n\n")
                        file_count += 1
                except Exception:
                    out.write(f"START OF FILE: {rel_path}\n")
                    out.write("[Binary or Unreadable Content]\n")
                    out.write(f"END OF FILE: {rel_path}\n\n")

    print(f"✅ Done! Saved to: {output_file}")
    print(f"📊 Processed {file_count} files.")

if __name__ == "__main__":
    main()