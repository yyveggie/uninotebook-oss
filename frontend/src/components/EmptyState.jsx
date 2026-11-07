import React from 'react'
import PropTypes from 'prop-types'

const EmptyState = ({ icon, text, subtext }) => (
    <div className="studio-empty-state">
        <div className="empty-state-icon">
            {icon}
        </div>
        <div className="empty-state-text">
            <p className="empty-state-text-main">{text}</p>
            {subtext && <p className="empty-state-text-sub">{subtext}</p>}
        </div>
    </div>
)

EmptyState.propTypes = {
    icon: PropTypes.node,
    text: PropTypes.string.isRequired,
    subtext: PropTypes.string
}

export default EmptyState
