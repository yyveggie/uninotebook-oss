import React from 'react'
import PropTypes from 'prop-types'
import './RightPanel.css' // Ensure generic back-btn styles are loaded if not globally available

const BackButton = ({ onClick, className = '', color = '#111827' }) => {
    return (
        <button
            className={`back-btn ${className}`}
            onClick={onClick}
        >
            <svg
                className="back-btn-icon"
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                stroke={color}
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
            >
                <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
        </button>
    )
}

BackButton.propTypes = {
    onClick: PropTypes.func.isRequired,
    className: PropTypes.string,
    color: PropTypes.string
}

export default BackButton
