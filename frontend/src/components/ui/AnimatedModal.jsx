import React, { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import './AnimatedModal.css'

const AnimatedModal = ({
    isOpen,
    onClose,
    children,
    size = 'md', // sm, md, lg, xl
    width, // Optional override
    height = 'auto',
    className = '',
    style = {},
    showCloseButton = true,
    closeOnOverlayClick = true
}) => {
    // Lock body scroll when modal is open
    useEffect(() => {
        if (isOpen) {
            document.body.style.overflow = 'hidden'
        } else {
            document.body.style.overflow = 'unset'
        }
        return () => {
            document.body.style.overflow = 'unset'
        }
    }, [isOpen])

    // Avoid running on server side if next.js (though this is react app)
    if (typeof window === 'undefined') return null

    return createPortal(
        <AnimatePresence>
            {isOpen && (
                <Motion.div
                    className="animated-modal-overlay"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    onClick={closeOnOverlayClick ? onClose : undefined}
                    style={{ zIndex: 1000 }} // Ensure high z-index
                >
                    <Motion.div
                        className={`animated-modal-content animated-modal-size-${size} ${className}`}
                        initial={{ scale: 0.95, opacity: 0, y: 20 }}
                        animate={{ scale: 1, opacity: 1, y: 0 }}
                        exit={{ scale: 0.95, opacity: 0, y: 20 }}
                        transition={{
                            type: "spring",
                            damping: 25,
                            stiffness: 300,
                            duration: 0.2
                        }}
                        onClick={(e) => e.stopPropagation()}
                        style={{
                            ...(width ? { width } : {}),
                            height,
                            ...style
                        }}
                    >
                        {showCloseButton && (
                            <button className="animated-modal-close-btn" onClick={onClose} aria-label="Close">
                                ×
                            </button>
                        )}
                        {children}
                    </Motion.div>
                </Motion.div>
            )}
        </AnimatePresence>,
        document.body
    )
}

export default AnimatedModal
