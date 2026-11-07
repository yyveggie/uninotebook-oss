import React, { useRef } from "react";
import { motion as Motion, useInView } from "framer-motion";
import { cn } from "../../lib/utils";

const sizeStyles = {
    sm: { fontSize: "1.125rem", lineHeight: "1.75rem" },
    md: { fontSize: "1.5rem", lineHeight: "2rem" },
    lg: { fontSize: "2.25rem", lineHeight: "2.5rem" },
    xl: { fontSize: "3rem", lineHeight: "1" },
    "2xl": { fontSize: "3.75rem", lineHeight: "1" },
};

const alignStyles = {
    left: { textAlign: "left" },
    center: { textAlign: "center" },
    right: { textAlign: "right" },
};

const variantStyles = {
    default: { color: "#ffffff" },
    muted: { color: "#9ca3af" }, // gray-400
    accent: { color: "#34d399" }, // emerald-400
    primary: { color: "#10b981" }, // emerald-500
    dark: { color: "#0f172a" }, // slate-900 (dark text for landing page)
};

export function ScrollReveal({
    children,
    containerClassName,
    textClassName,
    style = {},
    textStyle = {},
    baseOpacity = 0.1,
    threshold = 0.5,
    duration = 0.8,
    size = "lg",
    align = "left",
    variant = "default",
}) {
    const containerRef = useRef(null);
    const isInView = useInView(containerRef, {
        amount: threshold,
        once: false,
    });

    const computedTextStyle = {
        ...sizeStyles[size],
        ...alignStyles[align],
        ...variantStyles[variant],
        fontWeight: 600,
        lineHeight: 1.5,
        margin: 0,
        ...textStyle,
    }

    return (
        <Motion.div
            ref={containerRef}
            style={style}
            className={cn(containerClassName)}
            initial={{ opacity: baseOpacity }}
            animate={{ opacity: isInView ? 1 : baseOpacity }}
            transition={{ duration, ease: "easeOut" }}
        >
            <p
                className={cn(textClassName)}
                style={computedTextStyle}
            >
                {children}
            </p>
        </Motion.div>
    );
}

export default ScrollReveal;
