import os

diagrams_dir = "/home/rafaelcoelho/Workbench/COELHONexus/diagrams"

def compile():
    print("Compiling index.html...")
    
    # Read template
    template_path = os.path.join(diagrams_dir, "viewer_template.html")
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    # Read SVGs
    arch_path = os.path.join(diagrams_dir, "coelho_nexus_architecture.svg")
    with open(arch_path, "r", encoding="utf-8") as f:
        arch_svg = f.read()

    ai_path = os.path.join(diagrams_dir, "coelho_nexus_ai_domains.svg")
    with open(ai_path, "r", encoding="utf-8") as f:
        ai_svg = f.read()

    # Clean SVGs to strip any XML headers so they inline correctly
    if arch_svg.startswith("<?xml"):
        arch_svg = arch_svg[arch_svg.find("<svg"):]
    if ai_svg.startswith("<?xml"):
        ai_svg = ai_svg[ai_svg.find("<svg"):]

    # Replace placeholders
    output = template.replace("<!-- ARCHITECTURE_SVG -->", arch_svg)
    output = output.replace("<!-- AI_DOMAINS_SVG -->", ai_svg)

    # Write output
    index_path = os.path.join(diagrams_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(output)

    print("index.html compiled successfully.")

if __name__ == "__main__":
    compile()
