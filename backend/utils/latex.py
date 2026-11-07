"""
Backend LaTeX Normalizer (Chunk-Safe)

Only performs atomic transformations that work correctly on individual chunks.
Line-based equation wrapping is delegated to the frontend.
"""
import re


def fix_latex_format(text: str) -> str:
    r"""
    Chunk-safe LaTeX normalization.
    
    Only performs:
    1. Unicode to LaTeX command conversion (atomic, per-character)
    2. Delimiter normalization (\[...\] -> $$...$$)
    
    Does NOT perform line-based wrapping (requires full context).
    """
    if not text:
        return text
    
    # ============ Unicode Conversion (Chunk-Safe) ============
    
    unicode_subscripts = {
        '₀': '_0', '₁': '_1', '₂': '_2', '₃': '_3', '₄': '_4',
        '₅': '_5', '₆': '_6', '₇': '_7', '₈': '_8', '₉': '_9',
        'ₐ': '_a', 'ₑ': '_e', 'ₕ': '_h', 'ᵢ': '_i', 'ⱼ': '_j',
        'ₖ': '_k', 'ₗ': '_l', 'ₘ': '_m', 'ₙ': '_n', 'ₒ': '_o',
        'ₚ': '_p', 'ᵣ': '_r', 'ₛ': '_s', 'ₜ': '_t', 'ᵤ': '_u',
        'ᵥ': '_v', 'ₓ': '_x',
        '₊': '_+', '₋': '_{-}', '₌': '_=', '₍': '_(', '₎': '_)',
    }
    
    unicode_superscripts = {
        '⁰': '^0', '¹': '^1', '²': '^2', '³': '^3', '⁴': '^4',
        '⁵': '^5', '⁶': '^6', '⁷': '^7', '⁸': '^8', '⁹': '^9',
        'ⁿ': '^n', 'ⁱ': '^i', '⁺': '^+', '⁻': '^-', '⁼': '^=',
        '⁽': '^(', '⁾': '^)',
    }
    
    unicode_greek = {
        'α': r'\alpha', 'β': r'\beta', 'γ': r'\gamma', 'δ': r'\delta',
        'ε': r'\epsilon', 'ζ': r'\zeta', 'η': r'\eta', 'θ': r'\theta',
        'ι': r'\iota', 'κ': r'\kappa', 'λ': r'\lambda', 'μ': r'\mu',
        'ν': r'\nu', 'ξ': r'\xi', 'π': r'\pi', 'ρ': r'\rho',
        'σ': r'\sigma', 'τ': r'\tau', 'υ': r'\upsilon', 'φ': r'\phi',
        'χ': r'\chi', 'ψ': r'\psi', 'ω': r'\omega',
        'Γ': r'\Gamma', 'Δ': r'\Delta', 'Θ': r'\Theta', 'Λ': r'\Lambda',
        'Ξ': r'\Xi', 'Π': r'\Pi', 'Σ': r'\Sigma', 'Φ': r'\Phi',
        'Ψ': r'\Psi', 'Ω': r'\Omega',
    }
    
    unicode_math = {
        '∑': r'\sum', '∏': r'\prod', '∫': r'\int', '∂': r'\partial',
        '∇': r'\nabla', '√': r'\sqrt', '∞': r'\infty', '≈': r'\approx',
        '≠': r'\neq', '≤': r'\leq', '≥': r'\geq', '±': r'\pm',
        '×': r'\times', '÷': r'\div', '→': r'\rightarrow', '←': r'\leftarrow',
        '↔': r'\leftrightarrow', '∈': r'\in', '∉': r'\notin', '⊂': r'\subset',
        '⊃': r'\supset', '∪': r'\cup', '∩': r'\cap', '∅': r'\emptyset',
        '∀': r'\forall', '∃': r'\exists', '¬': r'\neg', '∧': r'\land',
        '∨': r'\lor', '⊕': r'\oplus', '⊗': r'\otimes',
    }
    
    for unicode_char, latex in unicode_subscripts.items():
        text = text.replace(unicode_char, latex)
    for unicode_char, latex in unicode_superscripts.items():
        text = text.replace(unicode_char, latex)
    for unicode_char, latex in unicode_greek.items():
        text = text.replace(unicode_char, latex)
    for unicode_char, latex in unicode_math.items():
        text = text.replace(unicode_char, latex)
    
    # ============ Delimiter Normalization (Chunk-Safe) ============
    # These work on any chunk because they don't rely on line structure
    
    # \( ... \) -> $ ... $
    text = re.sub(r'\\\((.*?)\\\)', r'$\1$', text)
    
    # \[ ... \] -> $$ ... $$
    text = re.sub(r'\\\[(.*?)\\\]', r'$$\1$$', text, flags=re.DOTALL)

    return text
