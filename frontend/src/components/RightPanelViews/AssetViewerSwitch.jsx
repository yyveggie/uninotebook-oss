import React from 'react'
import PPTViewer from '../PPTViewer'
import PosterViewer from '../PosterViewer'

const AssetViewerSwitch = ({
  view,
  tempPpt,
  tempPoster,
  onClosePpt,
  onClosePoster,
}) => {
  if (view === 'ppt') {
    return (
      <PPTViewer
        pptData={tempPpt}
        onClose={onClosePpt}
      />
    )
  }

  if (view === 'poster') {
    return (
      <PosterViewer
        posterData={tempPoster}
        onClose={onClosePoster}
      />
    )
  }

  return null
}

export default AssetViewerSwitch